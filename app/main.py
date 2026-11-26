"""TikTok Automation Engine — control API."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, engine, get_db, init_db
from app.keeper import SessionKeeper, renewal_loop
from app.memory import snapshot
from app.models import AppState, Job
from app.operations import CATEGORIES, WRITE_OPERATIONS, describe
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
boot_task: asyncio.Task | None = None
tt_adapter = None

# The bot and its services.
bot_engine = None
bot_dispatcher = None
bot_runner = None
scheduler = None
aliases = None
autoposter = None
janitor_task: asyncio.Task | None = None
keepalive_task: asyncio.Task | None = None

# Timeouts (seconds) so nothing during boot can hang forever.
DB_INIT_TIMEOUT = 60
ADAPTER_START_TIMEOUT = 120
RENEW_TIMEOUT = 60
ATTACH_TIMEOUT = 30
STARTED_AT = time.time()


# --------------------------------------------------------------------------
# the session keeper
# --------------------------------------------------------------------------
async def _start_keeper(keeper: SessionKeeper) -> None:
    """Renew once now, check the attachment, then keep renewing."""
    global renewal_task

    try:
        if settings.session_renew_on_boot:
            await asyncio.wait_for(keeper.renew(), timeout=RENEW_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "the first session renewal did not work: %s: %s", type(exc).__name__, exc
        )

    if settings.session_verify_on_boot:
        try:
            attached = await asyncio.wait_for(
                keeper.check_attached(), timeout=ATTACH_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001
            attached = False
            log.warning(
                "the attach check did not finish: %s: %s", type(exc).__name__, exc
            )
        if attached:
            log.info("attached to the account")
        else:
            log.warning(
                "the account did not answer as signed in — check TIKTOK_SESSION: it "
                "must hold the sessionid, and TikTok only honours a sessionid when "
                "the request also names the edge (tt-target-idc)"
            )

    # Always start the renewal loop, even if the checks above failed.
    renewal_task = asyncio.create_task(renewal_loop(keeper))


async def _boot_tiktok(adapter) -> None:
    """Start the client and the keeper in the background.

    Runs after the port is bound, so a slow or failing browser can never
    keep the API from answering /health.
    """
    global keeper

    try:
        await asyncio.wait_for(adapter.start(), timeout=ADAPTER_START_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "could not start the TikTok client: %s: %s | the API stays up, but every "
            "TikTok call will fail until a session is installed: "
            "python tools/session_from_cookies.py --file <cookies.txt> "
            "(or POST /admin/session)",
            type(exc).__name__,
            exc,
        )
        return

    keeper = SessionKeeper(adapter)
    # A call that comes back unauthenticated re-mints the session
    # and is retried once.
    adapter.on_auth_failure = keeper.heal
    try:
        await _start_keeper(keeper)
    except Exception:  # noqa: BLE001
        log.exception("the session keeper failed to start")


# --------------------------------------------------------------------------
# the bot
# --------------------------------------------------------------------------
async def _build_bot(adapter) -> None:
    """Wire the chat bot, the scheduler and the aliases together."""
    global bot_engine, bot_dispatcher, bot_runner, scheduler, aliases, autoposter

    from app.bot.aliases import AliasStore
    from app.bot.autoposter import AutoPoster
    from app.bot.channel import CommentChannel, DmChannel, MultiChannel
    from app.bot.dispatcher import Dispatcher
    from app.bot.engine import BotEngine
    from app.bot.runner import BotRunner
    from app.scheduler import Scheduler
    from app.state import StateStore

    bot_engine = BotEngine(adapter)
    store = StateStore()

    aliases = AliasStore(store)
    await aliases.load()

    bot_dispatcher = Dispatcher(
        bot_engine,
        allowed_authors=settings.bot_allowed_author_set,
        cooldown=settings.bot_cooldown,
    )

    scheduler = Scheduler(dispatcher=bot_dispatcher, store=store)
    await scheduler.load()

    autoposter = AutoPoster(bot_engine, "./data/queue")
    autoposter.load_state()

    comment_channel = CommentChannel(
        bot_engine, video_id=settings.bot_video_id, marker=settings.bot_marker
    )
    dm_channel = DmChannel(bot_engine, marker=settings.bot_marker)

    if settings.bot_channel == "dm":
        channel = dm_channel
    elif settings.bot_channel == "comment":
        channel = comment_channel
    else:
        channel = MultiChannel([dm_channel, comment_channel])

    services = {
        "scheduler": scheduler,
        "aliases": aliases,
        "autoposter": autoposter,
        "dispatcher": bot_dispatcher,
        "channel": channel,
    }
    bot_dispatcher.services = services

    bot_runner = BotRunner(
        bot_engine,
        channel,
        bot_dispatcher,
        interval=settings.bot_poll_interval,
    )

    if settings.scheduler_enabled:
        asyncio.create_task(scheduler.run_forever(30.0))

    bot_runner.start_task()
    log.info("the bot is listening on the %s channel", channel.name)


async def _send_welcome() -> tuple[bool, str]:
    """Say hello on TikTok, so the operator knows the bot is alive.

    Returns ``(sent, detail)`` so the caller can tell the operator the truth:
    TikTok refuses state-changing calls from some IPs, and a cheerful
    "sent" on a message that never left the machine is worse than useless.
    """
    if bot_runner is None:
        return False, "the bot is not running yet"
    from app.bot.welcome import build_welcome_async, deliver_welcome

    try:
        text = settings.welcome_text or await build_welcome_async(
            bot_engine, marker=settings.bot_marker, interval=int(settings.bot_poll_interval)
        )
        ok, detail = await deliver_welcome(bot_runner.channel, text)
        if ok:
            log.info("the welcome message was sent: %s", detail)
        else:
            log.warning("the welcome message was not delivered: %s", detail)
        return ok, detail
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        log.warning("could not send the welcome message: %s", detail)
        return False, detail


# --------------------------------------------------------------------------
# background helpers
# --------------------------------------------------------------------------
async def _janitor_loop() -> None:
    """Release the browser when it has been idle, and keep the disk tidy."""
    while True:
        try:
            await asyncio.sleep(30)
            if tt_adapter is not None:
                await tt_adapter.release_idle_browser()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("the janitor had a problem: %s", exc)


async def _keepalive_loop() -> None:
    """Ping ourselves so Render does not freeze the free instance.

    A frozen instance still works — it just takes ~50 seconds to wake up,
    and the bot's session may have expired in the meantime.
    """
    import urllib.request

    while True:
        await asyncio.sleep(max(60.0, settings.keepalive_interval_seconds))
        try:
            port = int(os.environ.get("PORT", "8000"))
        except ValueError:
            port = 8000
        url = f"http://127.0.0.1:{port}/health"
        try:
            await asyncio.to_thread(
                lambda: urllib.request.urlopen(url, timeout=10).read()
            )
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_pool, job_queue, probe_adapter, keeper, renewal_task
    global boot_task, tt_adapter, janitor_task, keepalive_task

    await asyncio.wait_for(init_db(), timeout=DB_INIT_TIMEOUT)

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

        # The adapter and the queue are created immediately, but the browser
        # and the keeper start in the background so a slow browser can never
        # keep the API from answering /health.
        tt_adapter = TikTokAdapter()
        queue = InlineQueue(settings.worker_concurrency, handler=run_job)
        queue.set_context({"adapter": tt_adapter})
        await queue.start()
        job_queue = queue
        boot_task = asyncio.create_task(_boot_tiktok(tt_adapter))
        log.info("running with the in-process job queue (no Redis configured)")

    # The chat bot, the scheduler and the aliases.
    janitor_task = asyncio.create_task(_janitor_loop())
    if settings.keepalive_enabled:
        keepalive_task = asyncio.create_task(_keepalive_loop())

    if tt_adapter is not None:
        try:
            await _build_bot(tt_adapter)
        except Exception as exc:  # noqa: BLE001
            log.exception("the bot could not start: %s", exc)
    else:
        log.info("the bot waits for Redis/ARQ mode to be wired up")

    if settings.welcome_on_boot:
        asyncio.create_task(_send_welcome())

    yield  # <- the port opens here now, without waiting for the browser

    # ---- shutdown ----
    for task in (janitor_task, keepalive_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    janitor_task = keepalive_task = None
    if bot_runner is not None:
        bot_runner.stop()
        bot_runner = None
    if scheduler is not None:
        scheduler.stop()
        scheduler = None
    if boot_task is not None:
        boot_task.cancel()
        try:
            await boot_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        boot_task = None
    if renewal_task is not None:
        renewal_task.cancel()
        try:
            await renewal_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        renewal_task = None
    if job_queue is not None:
        await job_queue.stop()
    if tt_adapter is not None:
        try:
            await tt_adapter.stop()
        except Exception:  # noqa: BLE001
            log.exception("could not stop the TikTok client cleanly")
        tt_adapter = None
    if probe_adapter is not None:
        await probe_adapter.stop()
        probe_adapter = None
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.4.0", lifespan=lifespan)


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
        "memory": snapshot(),
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
        "bot": bot_runner.status() if bot_runner is not None else {"running": False},
    }


@app.get("/api/operations", dependencies=[Depends(auth)])
async def list_operations():
    from app.operations import by_category

    return {
        "operations": describe(),
        "by_category": by_category(),
        "categories": CATEGORIES,
        "write_operations": sorted(WRITE_OPERATIONS),
        "allow_write_ops": settings.allow_write_ops,
    }


@app.get("/api/commands", dependencies=[Depends(auth)])
async def list_commands():
    """Every command the bot understands, grouped for the UI."""
    from app.bot.commands import describe_by_category

    return {
        "commands": describe_by_category(),
        "categories": CATEGORIES,
    }


@app.post("/api/chat", dependencies=[Depends(auth)])
async def run_command(payload: dict):
    """Run a command the way the bot would.

    This is what the web UI's console talks to, so the phone UI and the
    TikTok chat share one code path.
    """
    if bot_dispatcher is None:
        raise HTTPException(503, "the bot is not running yet")
    text = str(payload.get("command") or payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "no command given")
    if not text.startswith(settings.bot_marker):
        text = f"{settings.bot_marker}{text}"
    reply = await bot_dispatcher.run_text(
        text, author_id=str(payload.get("author_id") or "web"), author_name="web"
    )
    return {"command": text, "reply": reply or ""}


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
# automations
# --------------------------------------------------------------------------
@app.get("/api/scheduler", dependencies=[Depends(auth)])
async def scheduler_jobs():
    if scheduler is None:
        return {"enabled": False, "jobs": []}
    return {"enabled": True, "jobs": await scheduler.list()}


@app.post("/api/scheduler", dependencies=[Depends(auth)])
async def scheduler_add(payload: dict):
    if scheduler is None:
        raise HTTPException(503, "the scheduler is not running")
    schedule = str(payload.get("schedule") or "").strip()
    command = str(payload.get("command") or "").strip()
    if not schedule or not command:
        raise HTTPException(400, "a schedule and a command are needed")
    from app.scheduler import ScheduleError

    try:
        job = await scheduler.add(schedule, command)
    except ScheduleError as exc:
        raise HTTPException(400, str(exc)) from exc
    return job.as_dict()


@app.delete("/api/scheduler/{job_id}", dependencies=[Depends(auth)])
async def scheduler_remove(job_id: str):
    if scheduler is None:
        raise HTTPException(503, "the scheduler is not running")
    return {"removed": await scheduler.remove(job_id)}


@app.get("/api/aliases", dependencies=[Depends(auth)])
async def list_aliases():
    if aliases is None:
        return {"aliases": []}
    return {"aliases": await aliases.list(), "commands": aliases.aliases}


@app.post("/api/aliases", dependencies=[Depends(auth)])
async def add_alias(payload: dict):
    if aliases is None:
        raise HTTPException(503, "the aliases are not loaded yet")
    name = str(payload.get("name") or "").strip()
    command = str(payload.get("command") or "").strip()
    if not name or not command:
        raise HTTPException(400, "a name and a command are needed")
    return {"result": await aliases.add(name, command)}


@app.delete("/api/aliases/{name}", dependencies=[Depends(auth)])
async def remove_alias(name: str):
    if aliases is None:
        raise HTTPException(503, "the aliases are not loaded yet")
    return {"result": await aliases.remove(name)}


@app.post("/api/welcome", dependencies=[Depends(auth)])
async def send_welcome():
    """Send the welcome message again, right now."""
    ok, detail = await _send_welcome()
    body: dict[str, object] = {"sent": ok, "detail": detail}
    if not ok:
        body["hint"] = (
            "TikTok refused the write from this IP — the text was saved to "
            "data/welcome.txt so you can post it by hand, and it will go out "
            "by itself from an IP that is allowed to write."
        )
    return body


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
