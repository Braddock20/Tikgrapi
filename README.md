# TikTok Automation Engine

A single-account TikTok automation service: a FastAPI control API, a chat bot
that lives **inside TikTok itself**, a cron-style automation runner, encrypted
session storage, and a control room that works on a phone.

Everything is designed around one constraint: **it has to run on Render's free
tier** — 512 MB of RAM, 0.5 vCPU, an ephemeral disk, and a service that is
frozen between visits.

---

## The shape of it

```
app/
├─ main.py            the control API and the lifecycle
├─ memory.py          the memory guard — what is free, what may start
├─ scheduler.py       cron-style automations
├─ state.py           a small key/value store for schedules and aliases
├─ worker.py          the job runner (shared by ARQ and the in-process queue)
├─ queue.py           RedisQueue / InlineQueue behind one interface
├─ db.py              async SQLAlchemy — Postgres or SQLite
├─ models.py          Job, AppState, SessionMeta
├─ operations.py      the registry: every operation, its backend, its safety class
├─ security.py        Fernet session store + cookie parsing
├─ sessions.py        upload / normalise / track / probe a session
├─ keeper.py          the session keeper: renew, heal, stay attached
├─ ratelimit.py       conservative pacing
├─ bot/
│  ├─ commands.py     every command, grouped into nine categories
│  ├─ dispatcher.py   message -> command -> reply
│  ├─ channel.py      the comment channel and the DM channel
│  ├─ runner.py       the polling loop
│  ├─ welcome.py      the welcome message
│  ├─ aliases.py      the operator's own commands
│  └─ autoposter.py   posting from a queue folder
├─ adapters/
│  ├─ browser.py      signed requests through a real Chromium (lazy)
│  ├─ http.py         plain HTTP for the endpoints that need no signature
│  ├─ upload.py       the creator upload pipeline
│  └─ tiktok.py       dispatcher: operation -> backend, plus response normalising
└─ web/               the control room — three files, no build step
```

---

## Fitting in 512 MB

This is the part that shapes everything else, so it is worth being precise.

A Chromium left running takes **300–450 MB**. On a 512 MB Render instance
that is not "slow" — it is an out-of-memory kill, and Render restarts the
process from scratch. So:

| | what it does | why |
|---|---|---|
| **`BACKEND=auto`** | starts with the cheap HTTP client; the browser is launched only when an operation genuinely needs a signed request | most requests never need it |
| **idle release** | the browser is shut down `BROWSER_IDUT_SECONDS` (default 180) after the last signed request | idle memory drops back to ~100 MB |
| **one browser** | the DM channel and the API share a single Chromium | two of them do not fit |
| **lazy pages** | the Messages page is only opened when a conversation actually exists | a second renderer costs ~300 MB |
| **request blocking** | videos, images, fonts and analytics beacons are refused | the renderer never builds what the engine never reads |
| **memory guard** | the browser refuses to start when there is less than `BROWSER_MIN_FREE_MB` free | a clear error instead of an OOM kill |
| **system Chrome** | `BROWSER_EXECUTABLE_PATH` points the engine at a Chromium you already have | skips a ~420 MB download in the image |
| **SQLite + in-process queue** | no Redis, no Postgres | the free tier has neither, and both cost memory |

Measured on the real account:

```
idle, HTTP only                ~100 MB
with the browser running       ~450 MB  (one Chromium, blocked resources)
two Chromiums (the old code)  ~1100 MB  ← this is what would not fit
```

`/health` reports `memory.rss_mb` and `memory.free_mb` so you can watch it.

---

## Deploying to Render

```bash
git push your-branch main
```

Then, from the Render dashboard: **New → Blueprint** → point it at the repo.
`render.yaml` sets everything up:

* the `free` plan, a 1 GB disk at `/data`
* `healthCheckPath: /health`
* `API_KEY` and `SESSION_ENCRYPTION_KEY` generated for you
* `TIKTOK_SESSION` left for you to paste

Paste your cookie header into `TIKTOK_SESSION` in the Render dashboard, and
the service comes up attached to your account.

To run it somewhere else:

```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# paste the key into SESSION_ENCRYPTION_KEY, the cookies into TIKTOK_SESSION
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/` — it redirects to `/ui/`.

---

## The bot talks to you on TikTok

The bot reads commands from a chat channel **inside TikTok** and answers in
the same place. Two channels are implemented:

* **comment** — the comments of one video are the conversation. Posting a
  comment is the reply. Verified live: both directions work.
* **dm** — the direct-message inbox. TikTok's web messaging runs over a
  protobuf WebSocket that only the site's own JavaScript speaks, so this
  channel does not re-implement that protocol: it drives the real Messages
  page and lets the site do the sending. That keeps it working when the
  protocol changes.

With `BOT_CHANNEL=auto` the bot tries DM first and falls back to comments.

### The welcome message

On boot the bot says hello on TikTok — so you do not have to work out how to
talk to it:

```
Hello @auren_and_i — your TikTok bot is awake.

Everything runs from here: type a command in this thread and the answer
comes straight back. Replies are read every 25s.

The marker is "/" — every command starts with it.

Commands (type them in this thread):
/help — every command
/help account|videos|people|engagement|discovery|inbox|publishing|automation
...
```

Send it again any time with `/welcome`, or with the **Say hello again** button
in the control room. The message is split to fit TikTok's 1500-character
comment limit.

**Where it goes.** First a direct message, then a comment on the account's own
video — the comment is the dependable channel, because TikTok's messaging
service answers `status_code 4` from datacenter IPs. If TikTok refuses the
write itself, the engine publishes through the page's own comment composer
instead, which the site signs itself. That route needs TikTok's React
application to boot, so it costs about 700 MB of memory: it is used only when
there is that much free (`UI_FALLBACK_MIN_FREE_MB`, default 450 MB), and on a
512 MB instance it is skipped rather than OOM-killed. When no channel will take
the message the text is written to `data/welcome.txt` so it is never lost, and
the control room says so honestly instead of claiming a send that never
happened.

> **A note on the DM channel.** TikTok only lets you start a direct message
> with an account you can reach — a mutual follow, or yourself through the
> mobile app's *Message yourself*. On the web the "new message" control is
> hidden for an account with no connections, and TikTok's messaging service
> answers `status_code 4` ("Server is currently unavailable") from
> datacenter IPs. The channel reports that plainly instead of failing
> quietly, and it starts working the moment a conversation exists — try the
> app's **Settings → Message yourself**, or have anyone send the account a
> message. The comment channel works everywhere, always.

---

## The commands

56 commands in nine categories. `/help` lists them on TikTok, `/help <group>`
lists one group, and the **Commands** tab in the control room lists them all
with a search box.

### account
```
/whoami  /me            the account this session belongs to
/profile <username>     a user's profile and stats
/settings               the account's own settings
/username <name>        is a username still available?
/setprofile <bio>       change the account's bio            (write)
```

### videos
```
/posts <user> [count]   a user's videos
/myvideos [count]       the account's own videos
/video <id>             one video in detail
/setvideo <id>          choose the video the chat runs on
/comments <id> [count]  comments on a video
/replies <vid> <cid>    replies to a comment
```

### people
```
/followers <user>       /following <user>
/search <word>          find people
/searchvideos <word>    find videos
/searchall <word>       search everything
/suggested [count]      accounts suggested to this account
/friends [count]        accounts that follow this account back
```

### engagement  *(all of these change the account)*
```
/like /unlike           /save /unsave        /share
/comment <vid> <text>   /reply <vid> <cid> <text>
/deletecomment          /likecomment
/follow <user>          /unfollow <user>
```

### discovery
```
/explore [count]        the Explore page
/foryou [count]         this account's recommendations
/hashtag <tag> [count]  /taginfo <tag>
/music <id>             /trending [count]
```

### inbox
```
/notifications [count]  the activity inbox
/unread                 unread counts
/dms                    the DM conversations
```

### publishing  *(write)*
```
/upload <path> [caption]    post a video
/postnext                  post the next queued video
/deletevideo <id>          delete one of the account's videos
/visibility <id> <0|1|2>   change a video's visibility
/queue                     what is waiting to be posted
```

### automation
```
/cron [list|add <schedule> <command>|remove <id>|run <id>]
/alias [list|add <name> <command>|remove <name>]
/automate            everything the automations know
/run <command>       run another command
```

### engine
```
/help [category]     /status     /memory     /ops [category]
/pause  /resume      /raw <operation> {json}   /welcome
```

The command catalogue is **56 commands** over **41 engine operations**.

### The automations

`/cron` takes a schedule and a command:

```
/cron add every 30m /status
/cron add every 2h /postnext
/cron add hourly /unread
/cron add daily 09:00 /myvideos
/cron add */15 * * * * /explore
```

Jobs are stored in the database, so they survive a restart, and they are run
by the same dispatcher the chat uses — an automation is exactly a command
that runs on its own.

`/alias` gives a short name to a command you use often:

```
/alias add vids /myvideos
/alias add post /postnext
```

---

## The control room

`http://localhost:8000/ui/` — three files (`app/web/index.html`,
`app/web/app.css`, `app/web/app.js`), no build step, no npm, no CDN. The
container needs nothing from the internet to serve it.

It is built for a phone first:

* a bottom tab bar on small screens, a tab strip on large ones
* `env(safe-area-inset-*)` throughout, so it clears the notch and the home bar
* 16 px inputs, so iOS never zooms when you focus one
* 48 px minimum tap targets
* every panel is one column on a phone and two on a desktop
* `dvh` units, so the browser chrome does not break the layout

The look: a dark theme by default that follows the system, an aurora that
drifts behind the glass, hairline borders, one accent gradient, and motion
that is reduced to nothing when the system asks for that.

Three bundled variable fonts — Inter, Plus Jakarta Sans and JetBrains Mono,
107 KB in total, subset to latin. No request leaves the server to render a
letter.

**The four tabs**

* **Console** — type a command and watch the answer. The same code path the
  TikTok chat uses, so the phone is a first-class way to drive the engine.
* **Commands** — every command, grouped, searchable, one tap to run.
* **Automations** — the schedules and the aliases, add and remove.
* **Status** — the session, the memory, the browser, and the live catalogue of
  every operation the engine can perform.

The API key is kept in the browser's `localStorage` and is never sent
anywhere but this service.

---

## The session

The session is a credential: encrypted at rest with Fernet, never returned by
the API. Only a masked hint (`b1ee9c…ca64`) and a SHA-256 hash are stored.

```bash
# from a browser cookie header
python tools/session_from_cookies.py --file cookies.txt --probe

# or through the API
curl -H "X-API-Key: $API_KEY" -H "Content-Type: text/plain" \
     --data-binary @cookies.txt http://localhost:8000/admin/session
```

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
session was issued on.

### The permanent attachment

A TikTok session is two things at once:

| | cookies | lifetime |
|---|---|---|
| **durable** | `sessionid`, `tt-target-idc`, `odin_tt` | months — dies when *you* log out |
| **volatile** | `msToken`, `ttwid`, `tt_csrf_token` | hours — the site re-mints them |

The engine re-mints the volatile half on its own: at boot, every
`SESSION_RENEW_HOURS` hours (default 6), and the moment a call comes back
unauthenticated. The durable half lives in `TIKTOK_SESSION`, so a redeploy
never loses the account.

### The cookie rules that matter

1. **Keep every cookie.** The `store-*` cookies carry the account's country.
   Dropping them makes TikTok stop recognising the session.
2. **Drop `tt-target-idc` + `tt-target-idc-sign` for plain HTTP only.** The
   pair describes the CDN edge that issued it. With a stale pair, TikTok's
   unsigned endpoints answer `200` with an empty body.

---

## What was measured, and what was not

Every command was run against the live account (`auren_and_i`,
uid `7712199357596075015`) from a datacenter IP.

**Verified working:** the session attaches; the account's own profile and
videos are read; comments are read; a comment is published and deleted; a
follow is made and undone; the inbox and the unread counts answer; the
settings answer; `imSet: 1` — messaging is enabled on the account. The
**welcome message was published for real** on the account's own video, through
the page's own composer, and read back with the comment list.

**Known limits, all measured:**

* **A datacenter IP is degraded by TikTok.** Some endpoints answer with an
  empty body regardless of the session or the signature. A residential IP is
  materially better. Treat datacenter use as risky.
* **The write API is refused from here** — `/api/comment/publish/` answers
  with an empty body. The page's own composer works, and that is the route
  the engine takes, but it needs memory the free tier does not have.
* **`/api/post/item_list/` returns an empty body from here.** The site's own
  requests fail the same way. `/myvideos` reads the same list from the
  rendered profile page, which works.
* **The messaging service answers `status_code 4` from here.** That is why
  the DM channel reports itself unavailable instead of failing.
* **Likes are inconsistent.** The site's own like click returns an empty body
  from this IP while its unlike returns JSON, so `/like` is enabled but not
  fully verified.
* **The signature scheme moves.** The site now sends `X-Bogus=1` plus
  `X-Dynosaur` and a short `X-Gnarly`. A pure-Python re-implementation was
  tested and rejected by TikTok; the browser backend exists because the
  site's own JavaScript is currently the only signer that works reliably.

---

## The session cookie in this copy — read this first

This package ships a **real, working TikTok session cookie** in its `.env`
(`TIKTOK_SESSION`), so it can be deployed and tested without any setup. That
cookie is a live credential: anyone holding it is logged in as that account.

* `.env` is listed in `.gitignore` — never commit it.
* If you upload this zip anywhere public, **the cookie goes with it**. Delete
  `.env` before sharing, or replace the cookie with your own.
* After testing, **log out of TikTok in every session** (Security → Manage
  sessions → Log out all) to rotate the cookie, then paste a fresh one.
* On Render, set `TIKTOK_SESSION` as a secret environment variable and never
  let it into a build log, a commit, or a screenshot.

---

## Safety

Write commands are off by default and need two switches to agree:

* `ALLOW_WRITE_OPS=true` in the environment, and
* the operator's runtime switch — `/pause` turns it off, `/resume` turns it
  on.

The engine also paces itself: a minimum interval between requests and an
hourly ceiling, both on top of TikTok's own limits. Post on your own
schedule, but keep the interval long enough that it does not look automated.

---

## The CLI

```bash
python tools/session_from_cookies.py --file cookies.txt --probe
python tools/live_probe.py --all
python tools/bot.py --channel auto
python tools/welcome.py
python tools/autoposter.py --dir data/queue --interval 7200
```

---

## API

| | |
|---|---|
| `GET /health` | the service, the session and the memory |
| `GET /api/operations` | every operation, grouped |
| `GET /api/commands` | every command, grouped |
| `POST /api/chat` | run a command exactly as the bot would |
| `GET/POST/DELETE /api/scheduler` | the automations |
| `GET/POST/DELETE /api/aliases` | the custom commands |
| `POST /api/welcome` | say hello on TikTok again |
| `GET /jobs` · `POST /jobs` | the job queue |
| `GET /admin/session` | the stored session |
| `POST /admin/session/renew` | re-mint the device tokens |
| `POST /admin/pause` · `POST /admin/resume` | the write switch |

Every route except `/health` needs `X-API-Key: $API_KEY`.
