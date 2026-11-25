# Vendored `tiktokrapi`

`tiktokrapi` is **not published on PyPI** — `pip install tiktokrapi` fails with
404. The upstream project lives only on GitHub:

    https://github.com/Oyetomi/tiktok-web-api  (commit faaa3fe, "Initial commit")

It is vendored here so `pip install -r requirements.txt` works from a single
source tree.

## What was changed

Upstream ships the Python package but **not the JavaScript signer bundles it
imports**. `tiktokrapi/signer/worker.mjs` requires two files that do not exist
in the repository:

| file | required by | source used here |
|---|---|---|
| `signer/xbogus.js` | `signer/xbogus_entry.js` (`require('./xbogus')`) | npm [`xbogus`](https://www.npmjs.com/package/xbogus) 1.0.2 by iamatef (the implementation upstream's README calls "iamatef X-Bogus standalone") |
| `signer/xgnarly/*.js` | `signer/worker.mjs` (`import('xgnarly/encode.js')`) | [`carcabot/tiktok-xgnarly-decoded`](https://github.com/carcabot/tiktok-xgnarly-decoded) — the pure-JS reference encoder for webmssdk 5.1.3-ZTCA that upstream's README calls "patched carcabot 5.1.3-ZTCA encoder" |

Both are MIT/ISC licensed. `tiktokrapi/signer/xgnarly/` is copied verbatim
(`alphabet.js`, `cipher.js`, `decode.js`, `encode.js`, `payload.js`).

With these two files in place the upstream test suite passes:

    python -m pytest vendor/../tests/test_signer.py -q     # 5 passed

## Known limitation (as of 2026-11-25)

TikTok's web client has moved to a newer signature scheme: live browser
traffic now sends `X-Bogus=1` (a placeholder) together with an `X-Dynosaur`
parameter, and a shorter `X-Gnarly`. The vendored X-Bogus/X-Gnarly signer is
therefore accepted only by the older endpoints, and `X-Dynosaur` is not
implemented here.

That is why the engine's default backend is a **real browser** (`BACKEND=browser`):
the page's own JavaScript produces the current signature, so requests are
signed the way the site signs them. The plain-HTTP backend (`BACKEND=http`) is
used for the endpoints that answer without any signature.

The Python package itself is otherwise unmodified.
