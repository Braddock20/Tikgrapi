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
├─ keeper.py          the session keeper: renew, heal, stay attached
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

## The permanent attachment

This is the part that answers *"do sessions die after some time?"*.

**No — not while you keep the account logged in.** A TikTok session is two
things at once:

| | cookies | lifetime |
|---|---|---|
| **durable** | `sessionid`, `tt-target-idc`, `odin_tt` | months — dies when *you* log out, or change the password |
| **volatile** | `msToken`, `ttwid`, `tt_csrf_token` | hours — the site re-mints them on every visit |

The volatile half used to be the reason a bot "died": the sessionid was fine
but the device tokens had expired, and every call came back empty. The engine
now re-mints that half on its own:

* **at boot** — the session is installed from `TIKTOK_SESSION` and the browser
  visits the site, so a full set of device tokens is captured immediately;
* **every `SESSION_RENEW_HOURS` hours (default 6)** — the same, in the
  background, for as long as the process runs;
* **the moment a call comes back unauthenticated** — the session is re-minted
  and the call is retried once (`POST /admin/session/renew` does it by hand);
* **as a last resort** — if the durable half is gone too, the keeper falls
  back to the copy in `TIKTOK_SESSION` and tries again.

So the account stays attached as long as the environment variable holds a live
sessionid. Nothing else is needed, and nothing else expires on you.

### The one variable that holds it

`TIKTOK_SESSION` takes either shape:

```bash
# the whole cookie header (what the browser gives you) — recommended
TIKTOK_SESSION=sessionid=b1ee9c…; sessionid_ss=b1ee9c…; tt-target-idc=alisg; …

# or just the sessionid
TIKTOK_SESSION=<paste-your-sessionid-here>
```

A bare sessionid is expanded into the binding cookies automatically
(`sessionid`, `sessionid_ss` and the edge cookie `tt-target-idc`). That is
measured, not assumed:

| cookies sent | rendered page | inbox API |
|---|---|---|
| `sessionid` | guest — no account | `status_code 10202` (not logged in) |
| `sessionid` + `sessionid_ss` | guest — no account | `status_code 10202` |
| **`sessionid` + `tt-target-idc`** | **the account** | **real data** |

TikTok only honours a sessionid when the request also names the edge the
session was issued on. The engine pairs it with `tt-target-idc` for you
(override with `TIKTOK_TARGET_IDC`).

Verified end to end from a sessionid-only environment variable on 2026-11-26:
install → renew → `me` returns `auren_and_i` → comments read → **a video
posted** (`aweme_id 7712427397584260373`).

### What still ends it

* **You log out of TikTok** — the sessionid dies with it. Nothing can keep
  that alive; re-export the cookies and update `TIKTOK_SESSION`.
* **You change the password** — TikTok invalidates the session.
* **TikTok rotates the sessionid** — rare, but the keeper notices, keeps the
  new one and tells you (`sessionid_rotated` in the status, plus a log line).
  Update `TIKTOK_SESSION` to the value it prints.

### Watching it

```bash
curl http://localhost:8000/health          # session.attached, last_renewal, renewals, heals
curl -H "X-API-Key: $API_KEY" http://localhost:8000/admin/session
curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/admin/session/renew
```

`session.attached` is the field to alert on: `true` means the account really
answered as signed in, `false` means it did not.

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
| upload (post a video) | `status_code 0`, `aweme_id` returned, visible on the profile |

`user/detail` and `post/item_list` were also requested by a real browser from
this IP and returned empty bodies there too, so they are blocked by the
environment, not by the session. The engine therefore reads profiles from the
server-rendered page (`__UNIVERSAL_DATA_FOR_REHYDRATION__`), which works.

Measured again on 2026-11-26 from a **sessionid-only** session: install →
renew → `me` returns the account → comments read → a video posted
(`aweme_id 7712427397584260373`).

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

## Auto-posting

The upload pipeline was verified live against the account: a video posted in
about six seconds and appeared on the profile (`videoCount` went 0 → 1).
Posting uses TikTok's own creator pipeline, so **it does not need Chromium** —
it is a plain HTTP client.

### The autoposter

Drop videos into a queue folder and they go out on a schedule:

```bash
# one post every two hours
python tools/autoposter.py --dir data/queue --interval 7200

# post three now and exit — handy for cron
python tools/autoposter.py --dir data/queue --count 3 --interval 0

# captions, one per line, cycled
python tools/autoposter.py --dir data/queue --captions captions.txt

# a sidecar next to each video: clip.mp4 + clip.txt
python tools/autoposter.py --dir data/queue --default-caption "hello"

# hashtags, delete the file once posted, keep going when the queue empties
python tools/autoposter.py --dir data/queue --tag fyp tiktok --delete-after --loop
```

Caption resolution: sidecar `clip.txt` → next line of the caption list →
`--default-caption`. Hashtags from `--tag` are appended to every caption.

Posted files are recorded in `<dir>/.posted.json`, so a restart never
reposts, and a failed upload is remembered too — one broken file cannot block
the queue.

`ALLOW_WRITE_OPS=true` is required; the autoposter refuses to start without
it.

### From the bot

```
/upload <video_path> [caption]
/delete_video <video_id>
/visibility <video_id> <0 public|1 friends|2 private>
```

### Posting safety

TikTok penalises bulk posting from new accounts. The autoposter therefore
defaults to one post per hour (`--interval 3600`), and the engine's global
rate limiter and write gates apply on top. Post on your own schedule, but
keep the interval long enough that it does not look automated — the previous
account in this workspace was suspended after a single live test.


---

## The video engine

A second, independent half of the system: it *writes* the videos the
autoposter posts. It renders vertical compositions from a content library
and an asset library with Pillow and FFmpeg — no AI, no browser, no
manual design step.

```bash
# the fonts (OFL) and a starter library
python tools/fetch_fonts.py --dest assets/fonts
python tools/seed_library.py --content-dir content --assets-dir assets

# render and look at it — nothing is published
python -m video_engine.engine generate --preview

# render a batch and hand them to the autoposter queue
python -m video_engine.engine generate --count 5 --publish queue

# what has been happening
python -m video_engine.engine status
```

The engine picks the content, the mood, the visual identity (scrapbook,
diary, memory archive, editorial, letter, minimalist, playful), the layout
(one of twelve structures), the palette, the font pairing, the
decorations, the animation and the music — then renders, validates and
either queues or posts the result. Every choice is rule-driven and
seeded, so the output is varied but reproducible.

It talks to the posting system through one narrow door: a finished,
validated MP4 plus a caption sidecar in the autoposter's queue directory
(or, with `--publish upload`, the existing `upload` operation). Nothing in
the posting path was changed, and a failed render can never trigger a
post.

Full details — libraries, layouts, validation, environment variables — are
in [`video_engine/README.md`](video_engine/README.md).


---

## The control room

The same engine, with a browser in front of it.

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# then open http://localhost:8000/  (it redirects to /ui/)
```

Paste the `API_KEY` to unlock. It is the same key the machine API uses —
the UI keeps it in the browser's session storage and nothing else.

**What you can do from the browser**

* **Chat** — type `/generate 3` and watch the engine work; the live output
  streams into the conversation. `/status`, `/cleanup`, `/queue`,
  `/upload` and `/help` are there too.
* **Upload** — drop a file anywhere on the window. JSON/TXT/MD goes to the
  content library, images to the photographs or the decorations, sounds to
  the sound library, fonts to the font folders, video to the backgrounds.
* **Content** — the whole library, what has been used and what has not, with
  a delete button.
* **Library** — the asset pools and their sizes.
* **Renders** — every finished video as a thumbnail, with download and
  *Queue* buttons. Hovering plays the video.
* **Queue** — what is waiting for the autoposter.
* **Settings** — render profile, appearance and the key.

**The look** is deliberately quiet: system fonts, hairline borders, one
accent, white / grey / black in either theme, and a full-screen toggle in
the top bar for when you want the conversation and nothing else.

It is three files (`app/web/index.html`, `app/web/app.css`,
`app/web/app.js`) plus a small router (`app/webapi.py`). No build step, no
npm, no CDN — the container needs nothing from the internet to serve it.

**Safety.** The browser may only run `generate`, `status` and `cleanup`; any
other command is refused, and the engine's own write gates still apply to
publishing. A failed render still never reaches the poster.

---

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

# the account itself — a sessionid is enough
echo "TIKTOK_SESSION=<paste-your-sessionid-here>" >> .env

pip install -r requirements.txt
playwright install --with-deps chromium

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The session is installed from the environment at startup — there is no
`cookies.txt` to ship and no session file to keep alive. `tools/session_from_cookies.py`
is still there when you want to install a session from the command line
instead.

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
python -m pytest tests/ -q      # 108 passed
```

| File | Covers |
|---|---|
| `tests/test_api.py`, `test_keeper.py`, `test_bot.py`, `test_autoposter.py`, `test_operations.py`, `test_schemas.py`, `test_security.py` | the service, the session, the bot and the posting pipeline |
| `tests/test_video_engine.py` | the engine: libraries, composition, layouts, typography, validation, audio sync, duplicate prevention, the failure path |
| `tests/test_webui.py` | the control room: the pages, the API, the key, uploads, the command whitelist |

The suite runs offline: no TikTok, no browser, no network.

## Troubleshooting

**`could not start the TikTok client: SessionError: No session uploaded`**
— the API is fine; the TikTok client had no session to work with. It happens
on a fresh unpack because the session is a credential and is deliberately not
shipped in the zip. Set `TIKTOK_SESSION` and restart, or install it now:

```bash
python tools/session_from_cookies.py --file cookies.txt --probe
# or
curl -H "X-API-Key: $API_KEY" -H "Content-Type: text/plain" \
     --data-binary @cookies.txt http://localhost:8000/admin/session
```

**The account answers as a guest (`status_code 10202`, `session.attached`
is `false`)** — the sessionid is there but TikTok does not recognise it. Two
reasons: the environment holds a sessionid that is no longer valid (log in
again and export fresh cookies), or the edge cookie is missing. A bare
sessionid is paired with `tt-target-idc` automatically; if your session was
issued on another edge, set `TIKTOK_TARGET_IDC` to match it, or paste the
whole cookie header into `TIKTOK_SESSION`.

`GET /health` tells you the truth: `session.status` is `missing` until a
session is installed, then `unverified`, then `valid` or `invalid`;
`session.attached` says whether the account really answered.

**TikTok calls return an empty body** — the session was rejected or the
endpoint is blocked from your IP. Run `POST /admin/session/probe` and read
`session.detail`.

**Everything works but the account looks empty** — a brand-new account has no
videos, no followers and no following; that is real data, not a failure.

## Deployment

`docker compose up --build` for the full stack, or the `render.yaml` blueprint
for a single service with an in-process worker. See `render.yaml` for the
commented production shape (web + worker + Postgres + Redis).

The Render service needs **three** environment variables and nothing else:

| variable | what it is |
|---|---|
| `API_KEY` | the key your clients send in `X-API-Key` |
| `SESSION_ENCRYPTION_KEY` | a Fernet key — `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `TIKTOK_SESSION` | the account: your sessionid, or the whole cookie header |

No disk is needed: the session is rebuilt from `TIKTOK_SESSION` on every boot
and renewed while the service runs. `DATABASE_URL` defaults to SQLite next to
the app, `BACKEND` to `browser`, `ALLOW_WRITE_OPS` to `false` — change the
last one only when you mean it.

```bash
# render.yaml is a blueprint:
render blueprint launch   # or: New + Blueprint -> pick this repository
```

## Provenance

`vendor/tiktokrapi` is the upstream library with its two missing signer
bundles restored — see `vendor/README.md` for the exact sources and the
licences. Everything else in this repository is new work.
