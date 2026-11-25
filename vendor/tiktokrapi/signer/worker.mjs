// Persistent Node signer worker. JSON-RPC over stdin/stdout.
//
// Protocol:
//   IN:  {"id": 1, "method": "sign", "url": "...", "ua": "...", "counters"?: {...}, "ubcode"?: 8}
//   OUT: {"id": 1, "ok": true, "signedUrl": "...", "xBogus": "...", "xGnarly": "..."}
//   ERR: {"id": 1, "ok": false, "error": "..."}
//
//   IN:  {"id": 2, "method": "ping"}
//   OUT: {"id": 2, "ok": true, "pong": true}
//
// Bundle resolution: resolve relative to this file's __dirname.

import readline from "node:readline";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));

const xbogus = require(join(__dirname, "xbogus_entry.js"));
const { encode: xgnarly } = await import(join(__dirname, "xgnarly/encode.js"));

function send(obj) {
    process.stdout.write(JSON.stringify(obj) + "\n");
}

function sign(url, ua, counters = {}, ubcode = 8) {
    const queryStr = url.split("?")[1] || "";
    const xb = xbogus(url, ua);
    const xg = xgnarly(queryStr, "", ua, counters, { ubcode, sdkVersion: "1.0.0.368" });
    const sep = url.includes("?") ? "&" : "?";
    const signedUrl = `${url}${sep}X-Bogus=${xb}&X-Gnarly=${xg}`;
    return { xBogus: xb, xGnarly: xg, signedUrl };
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on("line", (line) => {
    let req;
    try {
        req = JSON.parse(line);
    } catch (e) {
        send({ id: null, ok: false, error: "invalid JSON: " + e.message });
        return;
    }
    const { id, method } = req;
    try {
        if (method === "ping") {
            send({ id, ok: true, pong: true });
        } else if (method === "sign") {
            if (!req.url || !req.ua) throw new Error("missing url or ua");
            const out = sign(req.url, req.ua, req.counters, req.ubcode);
            send({ id, ok: true, ...out });
        } else {
            throw new Error("unknown method: " + method);
        }
    } catch (e) {
        send({ id, ok: false, error: e.message });
    }
});

send({ id: 0, ok: true, ready: true });
