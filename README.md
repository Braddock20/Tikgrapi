# TikTok Automation Engine

A single-account TikTok automation service: a FastAPI control API, a job
queue, encrypted session storage, and an operation registry that only exposes
what has actually been verified against the live site.

The session belongs to the account `auren_and_i` (uid `7712199357596075015`).
Everything below was measured against that account on 2026-11-25.

---

## What the starter got wrong

The zip this was built from did not run. These are the defects found and
fixed while testing against the live site:

| # | Defect | Consequence | Fix |
|---|---|---|---|
| 1 | `requirements.txt` pinned `tiktokrapi`, which **is not on PyPI** | `pip install -r requirements.txt` fails outright | The library is vendored under `vendor/` with its missing signer bundles restored (see below) |
| 2 | Upstream `tiktokrapi` ships **no JS signer bundles** — `xbogus.js` and `xgnarly/encode.js` are referenced but absent | Every signed request raised `SignerError`; the library could not work at all | Restored from `npm xbogus` and `carcabot/tiktok-xgnarly-decoded`; the upstream signer tests now pass (5/5) |
| 3 | The library's "singleflight token refresh" on an empty-body response is a **no-op** (`pass` inside the lock) | It retried with the same dead token and reported a misleading error | Refreshed at the adapter level instead, and the empty-body case is now reported as a rejection rather than a silent retry |
| 4 | `upload_session` only accepted a JSON file | The browser's `Cookie:` header — what people actually have — was rejected | Accepts a cookie header (file or plain body) *or* a JSON export, and extracts the **sessionid** |
| 5 | `/health` reported only that the HTTP process was alive | The exact failure that hid the previous account suspension | `/health` now reports `session.status` (`missing` / `unverified` / `valid` / `invalid`) |
| 6 | The API called `redis_pool.enqueue_job` with `redis_pool = None` when Redis was unset | 500 on every job creation without Redis | A queue abstraction with an in-process worker; the service now runs with no external services |
| 7 | `Depends(SessionLocal)` on the endpoints | FastAPI introspected the sessionmaker and demanded a `local_kw` query parameter — every request 422'd | Proper `get_db` dependency |
| 8 | The in-process queue passed ARQ's reserved `_job_id` kwarg to the job function | `TypeError` on every job | Reserved kwargs are stripped, mirroring ARQ |
| 9 | `JobCreate.validate_operation` was dead code (not a Pydantic v2 validator) | Unknown operations passed schema validation | Real `@field_validator`, plus parameter checking |
| 10 | No rate limiting anywhere | A burst from one account is the fastest way to be flagged | A shared limiter: minimum interval + hourly ceiling, applied to every TikTok call |

---

## Architecture

```
app/
├─ main.py            FastAPI control API
├─ worker.py          the job runner (shared by ARQ and the in-process queue)
├─ queue.py           RedisQueue / InlineQueue behind one interface
├─ db.py              async SQLAlchemy — Postgres or SQLite
├─ models.py          Job, AppState, SessionMeta
├─ operations.py      the registry: every operation, its backend, its safety class
├─ security.py        Fernet session store + cookie parsing
├─ sessions.py        upload / normalise / track / probe a session
├─ ratelimit.py       conservative pacing
└─ adapters/
   ├─ browser.py      signed requests through a real Chromium
   ├─ http.py         plain HTTP for the endpoints that need no signature
   └─ tiktok.py       dispatcher: operation -> backend, plus response normalising
```

### Two backends

TikTok's web API is not one thing. The engine routes each operation to the
backend that can actually perform it:

* **browser** — drives a real Chromium. The page's own JavaScript signs each
  request, so requests carry the signature the site produces. The page is kept
  alive between calls, so a command costs one network round trip, not a page
  load.
* **http** — `curl_cffi` with a Chrome TLS fingerprint. Cheap and fast, but it
  can only reach the endpoints that answer without a signature.

### Operations

`GET /api/operations` returns the live catalogue. `verified` means the
operation was confirmed against the real account; `write` means it changes
account state.

| Operation | Backend | Write | Verified |
|---|---|---|---|
| `me` | ssr | | yes |
| `profile` | ssr | | yes |
| `comments` | browser | | yes |
| `followers` / `following` | http | | yes |
| `explore_feed` | browser | | yes |
| `comment` / `comment_delete` | browser | yes | yes (published **and** deleted live) |
| `follow` / `unfollow` | browser | yes | yes (state restored to 0 after the test) |
| `like` / `unlike` | browser | yes | partly — see the limitations |
| `user_posts` | browser | | no — blocked from this IP |
| `search_users`, `hashtag_videos` | browser | | no — not verified |

**Write operations are off by default.** `ALLOW_WRITE_OPS=false` refuses them
in the adapter itself, and `POST /admin/pause` is a second, runtime switch.

---

## Session handling

The session is a credential: encrypted at rest with Fernet, never returned by
the API. Only a masked hint (`b1ee9c…ca64`) and a SHA-256 hash are stored.

```bash
# from a browser cookie header
python tools/session_from_cookies.py --file cookies.txt --probe

# or through the API
curl -H "X-API-Key: $API_KEY" -H "Content-Type: text/plain" \
     --data-binary @cookies.txt http://localhost:8000/admin/session
```

```
sessionid : b1ee9c…ca64
cookies   : 31
stored    : 31 cookies (encrypted at unverified)
probe     : OK account=auren_and_i id=7712199357596075015 nickname='auren_and_i'
```

### The cookie rules that matter

Both were established by measurement, not guesswork:

1. **Keep every cookie.** The `store-*` cookies carry the account's country.
   Dropping them makes TikTok stop recognising the session — the API answers
   normally but the account is no longer logged in.
2. **Drop `tt-target-idc` + `tt-target-idc-sign` for plain HTTP only.** The
   pair describes the CDN edge that issued it. With a stale pair, TikTok's
   unsigned endpoints answer `200` with an empty body. The browser backend
   keeps the full set because it re-requests that pair from the site anyway.

---

## Live verification

`python tools/live_probe.py --all` runs every verified read operation against
the live site. Measured on 2026-11-25 from a datacenter IP:

| Endpoint | Result |
|---|---|
| `/foryou` + `/api/comment/list/` | real data (34–82 KB) |
| `/api/user/list/` (followers / following) | real data (18–38 KB) |
| `/api/comment/publish/` | "Comment sent successfully", comment id returned |
| `/api/comment/delete/` | success |
| `/api/commit/follow/user/` | success, `following` restored afterwards |
| `/api/user/detail/` | **empty body for every account** — blocked at IP level, not an account problem |
| `/api/post/item_list/` | **empty body** — the site's own requests fail the same way |

`user/detail` and `post/item_list` were also requested by a real browser from
this IP and returned empty bodies there too, so they are blocked by the
environment, not by the session. The engine therefore reads profiles from the
server-rendered page (`__UNIVERSAL_DATA_FOR_REHYDRATION__`), which works.

---

## Limitations — read this before deploying

* **A datacenter IP is degraded by TikTok.** Some endpoints answer with an
  empty body regardless of the session or the signature. A residential IP is
  materially better. The previous account in this workspace was suspended
  after a live test; treat datacenter use as risky.
* **The signature scheme moves.** The site now sends `X-Bogus=1` (a
  placeholder) plus `X-Dynosaur` and a new short `X-Gnarly`. A pure-Python
  re-implementation of X-Dynosaur was tested and **rejected** by TikTok
  (`"url doesn't match"`); the browser backend exists because the site's own
  JavaScript is currently the only signer that works reliably.
* **Likes are inconsistent.** The site's own like click returns an empty body
  from this IP while its unlike returns JSON, so `like` is enabled but not
  fully verified.
* **`user_posts` is blocked here** (see the table above).
* One browser process serves one account. Do not point two accounts at the
  same process.
* **The DM inbox is degraded from a datacenter IP** (`status_code 4`). The
  comment channel works; the DM channel needs a healthy IM conversation.

---

## The self-chat bot

The Baileys-style bot: commands are sent inside TikTok itself and the answers
come back the same way.

```bash
# 1. the account posts a video
# 2. point the bot at that video — its comment thread is the chat
python tools/bot.py --channel comment --video-id <video_id>

# 3. comment a command on the video, e.g.  /profile tatemcrae
#    the bot replies with another comment
```

Commands (prefixed with the marker, `/` by default):

```
/help  /whoami  /health  /profile <user>  /posts <user>  /comments <video_id>
/followers <user>  /following <user>  /like <video_id>  /unlike <video_id>
/comment <video_id> <text>  /follow <user>  /unfollow <user>  /search <keyword>
/pause  /resume  /raw <operation> {"json": "params"}
```

Safety rules the dispatcher enforces:

* only messages carrying the marker are considered;
* a message is handled once (the bot never answers its own replies);
* an allowlist can restrict commands to specific user ids;
* write commands need `ALLOW_WRITE_OPS=true` **and** the operator's pause
  switch to be off — both are checked before the command runs;
* a cooldown between commands, on top of the engine's global rate limit.

### Where the DM channel stands

TikTok's direct-message API was mapped from the site's own JavaScript:

| Endpoint | Purpose | Result from this IP |
|---|---|---|
| `/api/im/multi_user/` | conversation list | `400 illegal ID given` — signed request accepted, needs a conversation |
| `/api/im/item_detail/` | messages in a conversation | `400 illegal itemID given` — same |
| `/api/im/spotlight/relation/` | IM auxiliary | `status_code 4 — Server is currently unavailable` |
| `/api/inbox/notice_list/` | notifications inbox | works, empty |

The account has messaging enabled (`imSet: 1` in `/api/user/settings/`) and the
Messages page loads — but the inbox is empty, TikTok's IM service is degraded
from this IP, and **no "Message yourself" entry exists for this account on the
web**, so there is no self-conversation to read. The comment channel is
therefore the working self-chat today; `app/bot/channel.py` keeps a `DmChannel`
stub for when an IM conversation is available.


---

## Quick start

```bash
cp .env.example .env
# set API_KEY and SESSION_ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

pip install -r requirements.txt
playwright install --with-deps chromium

python tools/session_from_cookies.py --file cookies.txt --probe
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

With `REDIS_URL` empty the service uses SQLite and an in-process worker — no
Postgres, no Redis. Set `REDIS_URL` and `DATABASE_URL` to switch to the
ARQ + Postgres shape.

```bash
# health — the session status is the field to alert on, not "ok"
curl http://localhost:8000/health

# run an operation
curl -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
     -d '{"operation":"comments","params":{"video_id":"7684252586286927135"}}' \
     http://localhost:8000/jobs
```

## Tests

```bash
python -m pytest tests/ -q      # 50 passed
```

The suite runs offline: no TikTok, no browser, no network.

## Deployment

`docker compose up --build` for the full stack, or the `render.yaml` blueprint
for a single service with an in-process worker and a 1 GB disk for the
session. See `render.yaml` for the commented production shape (web + worker +
Postgres + Redis).

## Provenance

`vendor/tiktokrapi` is the upstream library with its two missing signer
bundles restored — see `vendor/README.md` for the exact sources and the
licences. Everything else in this repository is new work.
