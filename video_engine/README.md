# The video engine

An automated creative studio: it takes a library of words and a library of
pictures and sounds, and produces finished, varied, publish-ready vertical
videos — without a person choosing a template, a font or a colour.

There is no AI in it. Every decision (which layout, which palette, which
font pairing, where the words sit, when each element appears) is made by
rules and seeded randomness, so a run is reproducible and costs nothing but
CPU.

```
CONTENT LIBRARIES → SELECTION → MOOD/CLASSIFIER → ASSET SELECTION
      → COMPOSITION → LAYOUT → GRAPHICS (Pillow) → ANIMATION
      → VIDEO (FFmpeg) → AUDIO SYNC → VALIDATION → AUTOPOSTER → HISTORY
```

---

## Quick start

```bash
# 1. the fonts the engine pairs with (OFL-licensed, ~24 files)
python tools/fetch_fonts.py --dest assets/fonts

# 2. a starter library so the pipeline can run before your own material
python tools/seed_library.py --content-dir content --assets-dir assets

# 3. render one video and look at it (nothing is published)
python -m video_engine.engine generate --preview

# 4. render a batch and hand them to the autoposter queue
python -m video_engine.engine generate --count 5 --publish queue

# 5. what has been happening
python -m video_engine.engine status

# 6. unattended: produce a video every hour and queue it for the autoposter
python -m video_engine.engine generate --every 3600 --publish queue
```

---

## Running unattended

`--every <seconds>` keeps the engine running: it renders, validates, hands
the video over, sleeps, and does it again. It stops on `SIGTERM`/`SIGINT`
after the current run.

```bash
# one video every 30 minutes, straight into the autoposter queue
python -m video_engine.engine generate --every 1800 --publish queue
```

The engine keeps itself going:

* **content recycling** — when every item has been used, the least-used
  ones come back with a *new* composition instead of the run failing
* **self-healing renders** — a failed render is retried with a different
  seed, which produces a different composition
* **housekeeping** — the work directory is cleared and stale videos are
  pruned at the start of every batch
* **durable state** — content usage, compositions, sounds and render
  outcomes live in SQLite, so a restart picks up where it left off
* **one job at a time** — a render is CPU- and memory-heavy, so the queue
  never runs two at once

---

## What it decides by itself

| Decision | How it is made |
|---|---|
| Which content to use | Unused items first, weighted by mood/category fit. When the library runs dry the least-used items come back with a **new composition** — the system keeps running unattended. |
| The mood | The record's own `mood` field, else a fixed keyword lexicon (`video_engine/content/selector.py`). No model, no API. |
| How long | By content type: short thought 7–10 s, diary 12–15 s, memory 15–20 s, story 20–30 s. A `duration_hint` in the record wins. |
| The visual identity | One of seven families — scrapbook, diary, memory archive, editorial, letter, minimalist, playful — scored against the content's format, category and mood. |
| The layout | One of twelve structures, chosen from the identity's affinity list and rotated against the last 24 compositions. |
| The palette | Twelve curated palettes; the identity and the mood narrow the pool, recent ones are pushed down. |
| The typography | A *pair* chosen by rule (a handwritten face always sits with a readable companion), then sized to fill its box — never below 26 px on a 1080-wide frame. |
| The decorations | Density comes from the identity (0.12 for minimalist, 0.95 for playful) and every object is placed clear of the text. |
| The animation | Each element gets its own spec; entrances are staggered, text is revealed line by line or word by word, and the copy stays fully visible long enough to read. |
| The sound | Matched by mood and length, faded at both ends, looped when it is too short, and rotated so the same track does not repeat back to back. |

---

## Content libraries

Drop any `*.json`, `*.txt` or `*.md` file into `content/`. New files are
picked up automatically — no code change, no restart.

```json
{
  "id": "thought_001",
  "text": "Sometimes you miss the person you were before everything changed.",
  "category": "reflection",
  "mood": "nostalgic",
  "intensity": "medium",
  "format": "diary",
  "tags": ["memories", "growing_up"],
  "duration_hint": 15
}
```

Plain text works too — one item per line, or blank-line separated
paragraphs (`#` starts a comment). Malformed records are skipped and
reported, never rewritten. Duplicate wording is detected across files.
The wording of everything that *is* accepted is preserved exactly; long
items are split at sentence boundaries into pages, never rephrased.

## Asset library

```
assets/
├── backgrounds/{images,videos,textures}/   full-page grounds
├── photographs/                            the pictures in the collage
├── sounds/{nostalgic,piano,lofi,...}/      the music
├── fonts/{handwritten,serif,sans,typewriter,decorative}/
└── decorations/{stickers,tape,paper,stamps,doodles,frames}/
```

PNG / JPG / JPEG / WEBP, MP3 / WAV / AAC / M4A, MP4 for background video.
Everything is categorised by folder and then by file name. Anything the
engine can draw itself (tape, polaroids, stamps, doodles, torn paper,
grain) is drawn procedurally — so the output looks designed even with an
empty library.

---

## Rendering

| Profile | Resolution | FPS | Use |
|---|---|---|---|
| `standard` | 1080 × 1920 | 30 | Publishing |
| `light` | 720 × 1280 | 24 | Small instances / quick checks |

```bash
python -m video_engine.engine generate --profile light
```

Output is H.264 / AAC in MP4, `yuv420p`, faststart. Frames are streamed
straight into FFmpeg over a pipe — no frame files are written, and the
temporary directory is cleared after every job.

**Free-tier behaviour.** One render at a time, layers prepared once and
re-used, assets loaded lazily, peak memory measured at ~90–290 MB on a
720p/1080p render, and no model downloads, no GPU, no browser in the
rendering path.

Measured on a 2-core container (Pillow + FFmpeg, no GPU):

| Profile | Video length | Render time | Peak RSS |
|---|---|---|---|
| `light` (720×1280 @ 24) | 6–9 s | 6–8 s | 76–131 MB |
| `standard` (1080×1920 @ 30) | 8.5–17.5 s | 18–27 s | 107–186 MB |

Frames are streamed into the encoder over a pipe, so the disk stays
untouched during a render and nothing has to be cleaned up afterwards
except the small work directory.

---

## Publishing

The engine never replaces the posting system and never touches it.

```bash
# hand the finished video to the autoposter queue (safe default)
python -m video_engine.engine generate --publish queue

# post it directly through the existing upload operation
python -m video_engine.engine generate --publish upload
```

*`queue`* copies the MP4 and a sidecar `clip.txt` caption into the
autoposter's directory — the existing `AutoPoster` already understands
that pairing and posts on its own schedule.

*`upload*` goes through the same `upload` operation the API and the bot
use, so the account's write gates (`ALLOW_WRITE_OPS`, the operator's
pause switch) still apply.

A video is only handed over after it has been rendered **and** validated.
A failed render never reaches the poster.

In code:

```python
from video_engine.integration.autoposter import AutoPosterBridge

bridge = AutoPosterBridge("data/queue")
result = await bridge.submit(video_path, caption, mode="queue")
```

---

## Validation

Every finished file is checked before it can be published: it exists, it
decodes, the resolution matches the profile, it is vertical, the duration
is sane, the audio track is there, no sampled frame is blank, the text
stays inside the safe area, and no two text blocks collide. A composition
that fails is repaired once (type shrunk, boxes pulled in, collisions
nudged apart) and re-checked; if it still fails, the job is marked failed
and nothing is published.

---

## History

Everything is recorded in `data/video_engine/history.db` (SQLite, no extra
services): content usage, composition fingerprints (identity, layout, font
pair, palette, background, decorations), sound usage, render outcomes and
— when the numbers are fed back — performance per video.

Performance is optional and never blocks generation. When there is enough
data (100+ views per video), identities and layouts that engage better are
weighted slightly higher. Views alone are never treated as engagement.

---

## Layouts

`centered_diary` · `asymmetric_scrapbook` · `magazine_editorial` ·
`handwritten_letter` · `polaroid_collage` · `memory_archive` ·
`minimalist_typography` · `layered_cards` · `full_page_photo` ·
`typewriter_document` · `scattered_notes` · `layered_stationery`

Each layout is a *structure* — it decides which elements exist and how
they relate. Positions, rotation, colours, fonts, decorations and timing
are decided per render, so the same layout never looks the same twice.

---

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VE_CONTENT_DIR` | `./content` | where the libraries live |
| `VE_ASSETS_DIR` | `./assets` | where the assets live |
| `VE_OUTPUT_DIR` | `./data/video_engine/out` | finished videos |
| `VE_QUEUE_DIR` | `./data/queue` | the autoposter's queue |
| `VE_DB_PATH` | `./data/video_engine/history.db` | the history database |
| `VE_PROFILE` | `standard` | `standard` or `light` |
| `VE_SEED` | `0` | the base seed for reproducible runs |
| `VE_ALLOW_REUSE` | `false` | allow content to be used again |
| `VE_MAX_ATTEMPTS` | `3` | render attempts before a job is failed |
| `VE_PUBLISH` | `none` | `none`, `queue` or `upload` |

---

## Tests

```bash
python -m pytest tests/test_video_engine.py -v
```

Covers library loading and validation, duplicate detection, mood
classification, composition validity, layout variety, safe areas, audio
sync, duplicate prevention, the failure path (a failed render must never
post), the untouched autoposter, and the job queue's retry behaviour.
