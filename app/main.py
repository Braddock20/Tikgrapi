"""TikTok Automation Engine — control API."""
from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select

from app.config import settings
from app.db import SessionLocal, engine, get_db, init_db
from app.keeper import SessionKeeper, renewal_loop
from app.models import AppState, Job
from app.operations import WRITE_OPERATIONS, describe
from app.queue import InlineQueue, RedisQueue
from app.schemas import JobCreate
from app.sessions import (
    current_session_meta,
    install_session_from_env,
    record_verification,
    save_session_material,
)
from app.security import SessionError, delete_session, parse_session_material, session_exists
from app.worker import run_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("engine")

redis_pool = None
job_queue = None
probe_adapter = None
keeper: SessionKeeper | None = None
renewal_task: asyncio.Task | None = None


# --------------------------------------------------------------------------
# the session keeper
# --------------------------------------------------------------------------
async def _start_keeper(keeper: SessionKeeper) -> None:
    """Renew once now, check the attachment, then keep renewing."""
    global renewal_task

    try:
        if settings.session_renew_on_boot:
            await keeper.renew()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "the first session renewal did not work: %s: %s", type(exc).__name__, exc
        )

    if settings.session_verify_on_boot:
        if await keeper.check_attached():
            log.info("attached to the account")
        else:
            log.warning(
                "the account did not answer as signed in — check TIKTOK_SESSION: it "
                "must hold the sessionid, and TikTok only honours a sessionid when "
                "the request also names the edge (tt-target-idc)"
            )

    renewal_task = asyncio.create_task(renewal_loop(keeper))


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_pool, job_queue, probe_adapter, keeper, renewal_task

    await init_db()

    # The permanent attachment: the session described by TIKTOK_SESSION is
    # installed before anything tries to use it, so a fresh deployment comes
    # up already attached to the account.
    try:
        info = await install_session_from_env()
        if info.get("installed"):
            log.info("session installed from TIKTOK_SESSION")
        elif info.get("reason"):
            log.info("session not installed: %s", info["reason"])
    except SessionError as exc:
        log.error("TIKTOK_SESSION could not be installed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("could not install the session from TIKTOK_SESSION: %s", exc)

    if settings.uses_redis:
        from arq import create_pool
        from arq.connections import RedisSettings

        redis_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        job_queue = RedisQueue(redis_pool)
    else:
        from app.adapters.tiktok import TikTokAdapter

        adapter = TikTokAdapter()
        try:
            await adapter.start()
        except Exception as exc:  # noqa: BLE001
            # The API must still answer /health even if the browser cannot
            # start — the operator needs to see why, not a dead port.
            log.error(
                "could not start the TikTok client: %s: %s | the API stays up, but every "
                "TikTok call will fail until a session is installed: "
                "python tools/session_from_cookies.py --file <cookies.txt> "
                "(or POST /admin/session)",
                type(exc).__name__,
                exc,
            )
        else:
            keeper = SessionKeeper(adapter)
            # A call that comes back unauthenticated re-mints the session
            # and is retried once.
            adapter.on_auth_failure = keeper.heal
            await _start_keeper(keeper)
        queue = InlineQueue(settings.worker_concurrency, handler=run_job)
        queue.set_context({"adapter": adapter})
        await queue.start()
        job_queue = queue
        log.info("running with the in-process job queue (no Redis configured)")

    yield

    if renewal_task is not None:
        renewal_task.cancel()
        try:
            await renewal_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        renewal_task = None
    if job_queue is not None:
        await job_queue.stop()
    if probe_adapter is not None:
        await probe_adapter.stop()
        probe_adapter = None
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.3.0", lifespan=lifespan)

# --------------------------------------------------------------------------
# the control room
# --------------------------------------------------------------------------
# A browser front end for the engine and the session. It is a separate
# router so the machine API above stays exactly as it was.
from app.webapi import mount_ui, router as ui_router

app.include_router(ui_router)
mount_ui(app)


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
async def auth(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    if not settings.api_key or settings.api_key == "change-me":
        raise HTTPException(503, "Set a strong API_KEY before using the API")
    token = x_api_key
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or not secrets.compare_digest(token, settings.api_key):
        raise HTTPException(401, "Invalid API key")


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Service health.

    ``session.status`` reports whether the stored TikTok session was last
    seen working — the HTTP process being alive does not mean the account
    session still is. Alert on ``session.status``, not on ``ok``.
    """
    meta = await current_session_meta()
    present = session_exists()
    if not present:
        meta = {"status": "missing", "detail": "no session file installed"}
    paused = False
    async with SessionLocal() as db:
        state = await db.get(AppState, "writes_paused")
        paused = bool(state and state.value.get("paused"))
    session = {**(meta or {}), "present": present}
    if keeper is not None:
        kstatus = keeper.status()
        session.update(
            {
                "sessionid": kstatus.get("sessionid") or session.get("sessionid"),
                "attached": kstatus.get("attached"),
                "last_renewal": kstatus.get("last_renewal"),
                "renewals": kstatus.get("renewals"),
                "heals": kstatus.get("heals"),
                "renewals_hours": settings.session_renew_hours,
            }
        )
    return {
        "ok": True,
        "service": settings.app_name,
        "environment": settings.environment,
        "backend": settings.backend,
        "queue": "redis" if settings.uses_redis else "inline",
        "writes_paused": paused,
        "write_ops_enabled": settings.allow_write_ops,
        "session": session,
    }


@app.get("/api/operations", dependencies=[Depends(auth)])
async def list_operations():
    return {
        "operations": describe(),
        "write_operations": sorted(WRITE_OPERATIONS),
        "allow_write_ops": settings.allow_write_ops,
    }


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------
@app.post("/jobs", dependencies=[Depends(auth)])
async def create_job(body: JobCreate, db: AsyncSession = Depends(get_db)):
    from app.operations import get as get_operation

    spec = get_operation(body.operation)
    if spec is None:
        raise HTTPException(400, "Operation is not enabled")
    missing = spec.missing_params(body.params or {})
    if missing:
        raise HTTPException(400, f"{body.operation} requires: {', '.join(missing)}")
    if spec.write and not settings.allow_write_ops:
        raise HTTPException(
            403,
            f"'{body.operation}' changes account state — set ALLOW_WRITE_OPS=true to enable it",
        )

    if body.idempotency_key:
        existing = await db.scalar(
            select(Job).where(Job.idempotency_key == body.idempotency_key)
        )
        if existing:
            return {"job_id": existing.id, "status": existing.status, "deduplicated": True}

    job = Job(
        id=__import__("uuid").uuid4().hex,
        operation=body.operation,
        params=body.params,
        idempotency_key=body.idempotency_key,
    )
    db.add(job)
    await db.commit()
    try:
        await job_queue.enqueue_job("run_job", job.id, _job_id=job.id)
    except Exception as exc:  # noqa: BLE001
        job.status = "queue_error"
        job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        await db.commit()
        raise HTTPException(503, "Could not enqueue job") from exc
    return {"job_id": job.id, "status": "queued"}


@app.get("/jobs", dependencies=[Depends(auth)])
async def list_jobs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    limit = max(1, min(limit, 200))
    rows = (
        await db.scalars(select(Job).order_by(desc(Job.created_at)).limit(limit))
    ).all()
    return [serialize_job(x) for x in rows]


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return serialize_job(job)


def serialize_job(j: Job) -> dict:
    return {
        "id": j.id,
        "operation": j.operation,
        "status": j.status,
        "params": j.params,
        "result": j.result,
        "error": j.error,
        "attempts": j.attempts,
        "created_at": j.created_at.isoformat() if j.created_at else None,
    }


# --------------------------------------------------------------------------
# session administration
# --------------------------------------------------------------------------
@app.post("/admin/session", dependencies=[Depends(auth)])
async def upload_session(request: Request, file: UploadFile | None = File(default=None)):
    """Upload a TikTok session.

    Accepts a Cookie-Editor JSON export or a raw ``Cookie:`` header as a
    multipart file upload or a plain-text body. A bare sessionid works too.
    The sessionid is extracted and reported back in masked form only.
    """
    if file is not None:
        raw = await file.read()
    else:
        raw = await request.body()
    if not raw:
        raise HTTPException(400, "Send the session cookies as a file or request body")
    if len(raw) > 2_000_000:
        raise HTTPException(413, "Session too large")

    try:
        cookies = parse_session_material(raw)
        info = await save_session_material(raw, cookies=cookies)
    except SessionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"{type(exc).__name__}: {str(exc)[:300]}") from exc

    # A live worker holds its own client, so tell it to pick up the new session.
    await _notify_session_change()
    return info


@app.get("/admin/session", dependencies=[Depends(auth)])
async def session_info():
    meta = await current_session_meta()
    present = session_exists()
    return {
        "present": present,
        "session": {**(meta or {}), "present": present}
        if present
        else {"status": "missing", "detail": "no session file installed", "present": False},
        "keeper": keeper.status() if keeper is not None else {"status": "not running"},
    }


@app.post("/admin/session/renew", dependencies=[Depends(auth)])
async def renew_session():
    """Re-mint the session's device tokens right now.

    The keeper does this on its own every ``SESSION_RENEW_HOURS`` hours;
    this endpoint forces it, and reports whether the account is attached.
    """
    if keeper is None:
        raise HTTPException(503, "No session keeper is running in this process")
    try:
        result = await keeper.renew(force=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            502, f"Renewal failed: {type(exc).__name__}: {str(exc)[:300]}"
        ) from exc
    result["attached"] = await keeper.check_attached()
    return result


@app.post("/admin/session/probe", dependencies=[Depends(auth)])
async def probe_session():
    """Check the stored session against the live site."""
    global probe_adapter
    if not session_exists():
        raise HTTPException(400, "No session uploaded")

    from app.adapters.tiktok import TikTokAdapter

    if probe_adapter is None:
        probe_adapter = TikTokAdapter()
        try:
            await probe_adapter.start()
        except Exception as exc:  # noqa: BLE001
            await probe_adapter.stop()
            probe_adapter = None
            raise HTTPException(503, f"Could not start the TikTok client: {exc}") from exc

    try:
        result = await probe_adapter.call("me", {})
    except Exception as exc:  # noqa: BLE001
        await record_verification("invalid", f"{type(exc).__name__}: {str(exc)[:300]}")
        raise HTTPException(502, f"Session check failed: {type(exc).__name__}: {str(exc)[:300]}") from exc

    account = (result or {}).get("account") or {}
    await record_verification(
        "valid",
        detail=f"probed via {probe_adapter.backend_name}",
        username=account.get("unique_id"),
        account_id=str(account.get("id") or "") or None,
    )
    return {"valid": True, "account": account}


@app.delete("/admin/session", dependencies=[Depends(auth)])
async def remove_session():
    delete_session()
    return {"deleted": True}


async def _notify_session_change() -> None:
    """Best-effort: tell a running client to reload the stored session."""
    global probe_adapter
    if probe_adapter is not None:
        try:
            await probe_adapter.reload_session()
        except Exception:  # noqa: BLE001
            log.exception("could not reload the session in the probe client")
    if job_queue is not None and hasattr(job_queue, "_ctx"):
        adapter = job_queue._ctx.get("adapter")  # noqa: SLF001
        if adapter is not None:
            try:
                await adapter.reload_session()
            except Exception:  # noqa: BLE001
                log.exception("could not reload the session in the worker")


# --------------------------------------------------------------------------
# operator switches
# --------------------------------------------------------------------------
@app.post("/admin/pause", dependencies=[Depends(auth)])
async def pause(db: AsyncSession = Depends(get_db)):
    state = await db.get(AppState, "writes_paused")
    if not state:
        state = AppState(key="writes_paused", value={"paused": True})
        db.add(state)
    else:
        state.value = {"paused": True}
    await db.commit()
    return {"writes_paused": True}


@app.post("/admin/resume", dependencies=[Depends(auth)])
async def resume(db: AsyncSession = Depends(get_db)):
    state = await db.get(AppState, "writes_paused")
    if not state:
        state = AppState(key="writes_paused", value={"paused": False})
        db.add(state)
    else:
        state.value = {"paused": False}
    await db.commit()
    return {"writes_paused": False}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # pragma: no cover
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}"})
