/* ==========================================================================
   Control Room — the behaviour.
   No framework, no build step: one file, the API, and the DOM.
   ========================================================================== */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /* ── icons ──────────────────────────────────────────────────────────── */
  const ICONS = {
    chat: '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.4-.6L3 21l1.8-5.1A8.3 8.3 0 0 1 3.6 11 8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/>',
    text: '<path d="M4 6h16M4 12h16M4 18h10"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="m4 17 5-5 4 4 2.5-2.5L20 17"/>',
    film: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M8 4v16M16 4v16M3 9h5M3 15h5M16 9h5M16 15h5"/>',
    upload: '<path d="M12 16V4m0 0L8 8m4-4 4 4"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    send: '<path d="M4.5 12 20 4.5 14.5 20l-3-6-7-2z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    expand: '<path d="M8 3H4a1 1 0 0 0-1 1v4M16 3h4a1 1 0 0 1 1 1v4M8 21H4a1 1 0 0 1-1-1v-4M16 21h4a1 1 0 0 0 1-1v-4"/>',
    collapse: '<path d="M4 8h4V4M20 8h-4V4M4 16h4v4M20 16h-4v4"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14-4.5L4 9"/><path d="M4 5v4h4"/><path d="M4 13a8 8 0 0 0 14 4.5L20 15"/><path d="M20 19v-4h-4"/>',
    play: '<path d="M7 4.5 19 12 7 19.5z"/>',
    download: '<path d="M12 4v11m0 0 4-4m-4 4-4-4"/><path d="M4 18v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1"/>',
    trash: '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>',
    send2: '<path d="M4.5 12 20 4.5 14.5 20l-3-6-7-2z"/>',
    check: '<path d="m4 12 5 5L20 6"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    lock: '<rect x="4" y="10" width="16" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    cloud: '<path d="M7 18a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 17.5 10 4 4 0 0 1 17 18z"/>',
  };

  const icon = (name, size = 18) =>
    `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"
       stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;

  const el = (tag, cls, html) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  };

  /* ── state ──────────────────────────────────────────────────────────── */
  const state = {
    key: sessionStorage.getItem("ve_key") || "",
    profile: localStorage.getItem("ve_profile") || "standard",
    theme: localStorage.getItem("ve_theme") || "light",
    view: "chat",
    pollers: {},
  };

  /* ── api ────────────────────────────────────────────────────────────── */
  async function api(path, options = {}) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (state.key) opts.headers["X-API-Key"] = state.key;
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(`/ui/api${path}`, opts);
    if (res.status === 401) {
      showGate("That key was not accepted.");
      throw new Error("unauthorised");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    if (opts.raw) return res;
    return res.json();
  }

  /* ── toasts ─────────────────────────────────────────────────────────── */
  function toast(text, bad = false) {
    const node = el("div", `toast${bad ? " bad" : ""}`, text);
    $("#toasts").appendChild(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transition = "opacity .3s";
      setTimeout(() => node.remove(), 320);
    }, 3200);
  }

  /* ── the gate ───────────────────────────────────────────────────────── */
  function showGate(message) {
    $("#gate").hidden = false;
    $("#app").style.display = "none";
    if (message) $("#gate-error").textContent = message;
    setTimeout(() => $("#gate-key").focus(), 50);
  }

  async function unlock() {
    const key = $("#gate-key").value.trim();
    if (!key) return;
    state.key = key;
    try {
      await api("/status");
      sessionStorage.setItem("ve_key", key);
      $("#gate").hidden = true;
      $("#app").style.display = "";
      $("#gate-error").textContent = "";
      boot();
    } catch (err) {
      state.key = sessionStorage.getItem("ve_key") || "";
      showGate("That key was not accepted.");
    }
  }

  /* ── chat ───────────────────────────────────────────────────────────── */
  const scroll = $("#chat-scroll");

  function scrollToEnd() {
    requestAnimationFrame(() => { scroll.parentElement.scrollTop = scroll.parentElement.scrollHeight; });
  }

  function addUser(text) {
    const wrap = el("div", "msg msg-user");
    wrap.appendChild(el("div", "bubble", ""));
    wrap.querySelector(".bubble").textContent = text;
    scroll.appendChild(wrap);
    scrollToEnd();
  }

  function addSystem({ iconName = "spark", title = "Engine", body = "", html = null, console: out = null }) {
    const wrap = el("div", "msg msg-system");
    const card = el("div", "card");
    const head = el("div", "card-head");
    head.appendChild(el("span", "chip-icon", icon(iconName, 13)));
    head.appendChild(el("span", "card-title", title));
    card.appendChild(head);

    if (html) {
      const body_node = el("div", "card-body");
      body_node.appendChild(html);
      card.appendChild(body_node);
    } else if (body) {
      const body_node = el("div", "card-body");
      body_node.innerHTML = body;
      card.appendChild(body_node);
    }

    if (out) {
      const pre = el("pre", "console", "");
      pre.textContent = out;
      card.appendChild(pre);
    }

    wrap.appendChild(card);
    scroll.appendChild(wrap);
    scrollToEnd();
    return wrap;
  }

  function addConsoleCard(title) {
    const wrap = addSystem({ iconName: "clock", title });
    const pre = el("pre", "console", "");
    wrap.querySelector(".card").appendChild(pre);
    return { wrap, pre };
  }

  /* ── status ─────────────────────────────────────────────────────────── */
  async function refreshStatus() {
    let data;
    try {
      data = await api("/status");
    } catch (err) {
      toast(err.message, true);
      return;
    }

    const s = data.session || {};
    const dot = $("#session-dot");
    const present = s.present && s.status !== "invalid" && s.status !== "missing";
    dot.className = `dot ${present ? "is-good" : s.present ? "is-warn" : "is-bad"}`;
    $("#session-text").textContent = present
      ? (s.username || s.account_id || "signed in")
      : s.present ? `session ${s.status}` : "no session";

    $("#count-content").textContent = data.content.items;
    $("#count-assets").textContent = Object.values(data.assets.pools || {}).reduce((a, b) => a + b, 0);
    $("#count-renders").textContent = (data.recent_renders || []).length;
    $("#count-queue").textContent = (data.queue || []).length;

    const profile = data.engine.profile;
    $("#rail-meta").textContent = `${profile.width}×${profile.height} @ ${profile.fps}fps · ${data.assets.fonts} fonts · ${data.assets.sounds} sounds`;
    $("#brand-sub").textContent = `${data.content.items} items · ${data.content.remaining} unused`;

    $$("#profile-switch button").forEach((b) => b.classList.toggle("is-active", b.dataset.profile === state.profile));
    return data;
  }

  function statusCard(data) {
    const wrap = el("div");
    const stats = el("div", "stats");
    const rows = [
      [data.content.items, "content items"],
      [data.content.remaining, "unused"],
      [Object.values(data.assets.pools || {}).reduce((a, b) => a + b, 0), "assets"],
      [data.assets.fonts, "fonts"],
      [data.assets.sounds, "sounds"],
      [(data.renders.completed?.count) || 0, "renders"],
    ];
    rows.forEach(([value, label]) => {
      const stat = el("div", "stat", `<b>${value}</b><span>${label}</span>`);
      stats.appendChild(stat);
    });
    wrap.appendChild(stats);

    const recent = (data.recent_renders || []).slice(0, 5);
    if (recent.length) {
      const list = el("dl", "kv");
      recent.forEach((r) => {
        const dt = el("dt", "", r.content_id || "—");
        const dd = el("dd", "", `${r.duration ? r.duration.toFixed(1) : "?"}s video · ${r.render_time ? r.render_time.toFixed(1) : "?"}s render`);
        list.appendChild(dt);
        list.appendChild(dd);
      });
      wrap.appendChild(list);
    }
    return wrap;
  }

  /* ── commands ───────────────────────────────────────────────────────── */
  async function runCommand(raw) {
    const text = raw.trim();
    if (!text) return;

    const lower = text.toLowerCase();
    const verb = lower.replace(/^\//, "").split(/\s+/)[0];

    if (verb === "help") {
      addSystem({
        iconName: "spark",
        title: "Commands",
        body: `<p>Type any of these in the chat:</p>
               <p class="muted"><b>/generate 3</b> — render three videos<br/>
               <b>/status</b> — the engine at a glance<br/>
               <b>/cleanup</b> — clear temporary files<br/>
               <b>/queue</b> — open the autoposter queue<br/>
               <b>/upload</b> — choose files to add<br/>
               <b>/help</b> — this list</p>`,
      });
      return;
    }

    if (verb === "status") {
      const data = await refreshStatus();
      addSystem({ iconName: "check", title: "Status", html: statusCard(data) });
      return;
    }

    if (verb === "cleanup") {
      await runEngine("/command", { command: "cleanup" });
      return;
    }

    if (verb === "upload") {
      $("#file-input").click();
      return;
    }

    if (verb === "queue") {
      switchView("queue");
      return;
    }

    if (verb === "generate") {
      const match = text.match(/(\d+)/);
      const count = match ? Math.min(20, Math.max(1, parseInt(match[1], 10))) : 1;
      await runEngine("/generate", { count, profile: state.profile, publish: "none" });
      return;
    }

    addSystem({
      iconName: "spark",
      title: "Not sure",
      body: `<p>I did not recognise that. I can render videos, show the status, clean up, open the queue and upload files.</p>
             <p class="muted">Try <b>/help</b>.</p>`,
    });
  }

  async function runEngine(path, payload) {
    const { pre: out, wrap } = addConsoleCard("Working…");
    let job;
    try {
      job = await api(path, { method: "POST", body: payload });
    } catch (err) {
      out.textContent = err.message;
      out.className = "console err";
      return;
    }
    const lines = [];
    const paint = () => {
      out.textContent = lines.join("\n") || "starting…";
      scrollToEnd();
    };
    paint();

    const poll = setInterval(async () => {
      try {
        const fresh = await api(`/jobs/${job.id}`);
        if (fresh.lines.length !== lines.length) {
          lines.length = 0;
          lines.push(...fresh.lines);
          paint();
        }
        if (fresh.status !== "running") {
          clearInterval(poll);
          const title = wrap.querySelector(".card-title");
          title.textContent = fresh.status === "completed" ? "Done" : "Finished with errors";
          if (fresh.exit_code !== 0 && fresh.exit_code !== null) {
            out.className = "console err";
          } else {
            out.className = "console";
            lines.push("✓ finished");
            paint();
          }
          await refreshStatus();
          if (state.view === "renders") loadView("renders");
        }
      } catch (err) {
        clearInterval(poll);
      }
    }, 600);
  }

  /* ── uploads ────────────────────────────────────────────────────────── */
  async function uploadFiles(files) {
    for (const file of files) {
      const card = addConsoleCard(`Uploading ${file.name}`);
      try {
        const form = new FormData();
        form.append("file", file);
        const res = await api("/upload", { method: "POST", body: form });
        card.wrap.querySelector(".card-title").textContent = "Uploaded";
        card.pre.textContent = `stored → ${res.folder}\n${res.name} (${(res.bytes / 1024).toFixed(0)} KB)`;
        card.pre.className = "console ok";
      } catch (err) {
        card.wrap.querySelector(".card-title").textContent = "Upload failed";
        card.pre.textContent = err.message;
        card.pre.className = "console err";
      }
    }
    await refreshStatus();
    toast(`${files.length} file(s) added`);
  }

  /* ── views ──────────────────────────────────────────────────────────── */
  const VIEW_META = {
    chat:     ["Chat", "Ask for a video, upload material, or run an engine command."],
    content:  ["Content", "Everything the engine can publish, and what has already been used."],
    library:  ["Library", "The photographs, sounds, fonts and decorations on disk."],
    renders:  ["Renders", "Finished videos, ready to preview, download or queue."],
    queue:    ["Queue", "Videos waiting for the autoposter."],
    settings: ["Settings", "Profile, appearance and the API key."],
  };

  function switchView(name) {
    state.view = name;
    $$(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
    const [title, sub] = VIEW_META[name] || VIEW_META.chat;
    $("#view-title").textContent = title;
    $("#view-sub").textContent = sub;
    $("#view-chat").classList.toggle("is-active", name === "chat");
    $("#view-data").classList.toggle("is-active", name !== "chat");
    if (name !== "chat") loadView(name);
    if (window.innerWidth <= 860) $("#app").classList.remove("rail-open");
  }

  async function loadView(name) {
    const host = $("#data-scroll");
    host.innerHTML = "";
    const inner = el("div", "data-inner");
    host.appendChild(inner);

    try {
      if (name === "content") return await renderContent(inner);
      if (name === "library") return await renderLibrary(inner);
      if (name === "renders" || name === "queue") return await renderFiles(inner, name);
      if (name === "settings") return renderSettings(inner);
    } catch (err) {
      inner.appendChild(el("div", "empty", icon("cloud") + `<p>${err.message}</p>`));
    }
  }

  async function renderContent(inner) {
    const data = await api("/content");
    const head = el("div", "data-head");
    head.appendChild(el("div", "", `<h3>${data.items.length} of ${data.total} items</h3>
      <p>${data.report.accepted} accepted · ${data.report.rejected} rejected · ${data.report.duplicates} duplicates</p>`));
    inner.appendChild(head);

    const list = el("div", "list");
    data.items.forEach((item) => {
      const row = el("div", "item");
      const main = el("div", "item-main");
      main.appendChild(el("div", "item-text", ""));
      main.querySelector(".item-text").textContent = item.text;
      const tags = el("div", "item-tags");
      [item.category, item.mood, item.format].filter(Boolean).forEach((t) => tags.appendChild(el("span", "tag", t)));
      if (item.used) tags.appendChild(el("span", "tag used", `used ×${item.used}`));
      main.appendChild(tags);
      row.appendChild(main);

      const del = el("button", "icon-btn", icon("trash", 16));
      del.title = "Remove this item";
      del.onclick = async () => {
        try {
          await api("/content", { method: "DELETE", body: { id: item.id } });
          row.remove();
          toast("Removed");
          refreshStatus();
        } catch (err) { toast(err.message, true); }
      };
      row.appendChild(del);
      list.appendChild(row);
    });
    inner.appendChild(list);
  }

  async function renderLibrary(inner) {
    const data = await api("/status");
    const head = el("div", "data-head");
    head.appendChild(el("div", "", `<h3>Asset library</h3><p>${data.engine.assets_dir}</p>`));
    inner.appendChild(head);

    const grid = el("div", "grid");
    const pools = data.assets.pools || {};
    Object.entries(pools).forEach(([pool, count]) => {
      const tile = el("div", "tile");
      tile.appendChild(el("div", "tile-media", `<span class="placeholder">${icon("image", 26)}</span>`));
      const body = el("div", "tile-body");
      body.appendChild(el("div", "tile-name", pool));
      body.appendChild(el("div", "tile-meta", `${count} file(s)`));
      tile.appendChild(body);
      grid.appendChild(tile);
    });
    inner.appendChild(grid);
  }

  async function renderFiles(inner, kind) {
    const data = await api("/files");
    const files = data.files.filter((f) => (kind === "queue" ? f.kind === "queue" : f.kind !== "queue"));
    const head = el("div", "data-head");
    head.appendChild(el("div", "", `<h3>${kind === "queue" ? "Autoposter queue" : "Finished videos"}</h3>
      <p>${files.length} file(s)</p>`));
    inner.appendChild(head);

    if (!files.length) {
      inner.appendChild(el("div", "empty", icon("film", 34) + "<p>Nothing here yet.</p>"));
      return;
    }

    const grid = el("div", "grid");
    files.forEach((file) => {
      const tile = el("div", "tile");
      const media = el("div", "tile-media");
      // A still for the grid; the video itself takes over on hover. A still
      // loads instantly and works in every browser, including the ones that
      // cannot decode H.264.
      const poster = document.createElement("img");
      poster.src = `/ui/api/poster/${file.kind}/${file.name}?key=${encodeURIComponent(state.key)}`;
      poster.alt = file.name;
      poster.loading = "lazy";
      media.appendChild(poster);

      const video = document.createElement("video");
      video.src = file.url;
      video.muted = true;
      video.loop = true;
      video.playsInline = true;
      video.preload = "none";
      media.appendChild(video);

      const swap = (show) => {
        poster.style.display = show ? "none" : "block";
        video.style.display = show ? "block" : "none";
        if (show) video.play().catch(() => {});
        else video.pause();
      };
      media.addEventListener("mouseenter", () => swap(true));
      media.addEventListener("mouseleave", () => swap(false));
      tile.appendChild(media);

      const body = el("div", "tile-body");
      body.appendChild(el("div", "tile-name", file.name));
      body.appendChild(el("div", "tile-meta", `${(file.size / 1048576).toFixed(1)} MB · ${file.kind}`));
      tile.appendChild(body);

      const actions = el("div", "tile-actions");
      const dl = el("a", "btn", icon("download", 14) + "Download");
      dl.href = `${file.url}?key=${encodeURIComponent(state.key)}`;
      dl.download = file.name;
      actions.appendChild(dl);

      if (file.kind !== "queue") {
        const pub = el("button", "btn btn-accent", icon("upload", 14) + "Queue");
        pub.onclick = async () => {
          try {
            const res = await api("/publish", { method: "POST", body: { name: file.name } });
            toast(res.submitted ? "Queued for the autoposter" : res.detail, !res.submitted);
            refreshStatus();
          } catch (err) { toast(err.message, true); }
        };
        actions.appendChild(pub);
      }
      tile.appendChild(actions);
      grid.appendChild(tile);
    });
    inner.appendChild(grid);
  }

  function renderSettings(inner) {
    const head = el("div", "data-head");
    head.appendChild(el("div", "", "<h3>Settings</h3><p>How this browser talks to the engine.</p>"));
    inner.appendChild(head);

    const card = el("div", "card");
    const body = el("div", "card-body");

    const profileRow = el("div", "form-row");
    profileRow.appendChild(el("div", "", "<label>Render profile</label><div class='hint'>Standard is 1080×1920 @ 30fps. Light is 720×1280 @ 24fps.</div>"));
    const seg = el("div", "segmented");
    [["standard", "Standard"], ["light", "Light"]].forEach(([value, label]) => {
      const b = el("button", state.profile === value ? "is-active" : "", label);
      b.onclick = () => {
        state.profile = value;
        localStorage.setItem("ve_profile", value);
        $$("#profile-switch button").forEach((x) => x.classList.toggle("is-active", x.dataset.profile === value));
        $$("#profile-switch button").forEach((x) => x.classList.toggle("is-active", x.dataset.profile === value));
        seg.querySelectorAll("button").forEach((x, i) => x.classList.toggle("is-active", (i === 0) === (value === "standard")));
        toast(`Profile set to ${label}`);
      };
      seg.appendChild(b);
    });
    profileRow.appendChild(seg);
    body.appendChild(profileRow);

    const themeRow = el("div", "form-row");
    themeRow.appendChild(el("div", "", "<label>Appearance</label><div class='hint'>Light, dark — or follow the system.</div>"));
    const themeSeg = el("div", "segmented");
    [["light", "Light"], ["dark", "Dark"]].forEach(([value, label]) => {
      const b = el("button", state.theme === value ? "is-active" : "", label);
      b.onclick = () => setTheme(value);
      themeSeg.appendChild(b);
    });
    themeRow.appendChild(themeSeg);
    body.appendChild(themeRow);

    const keyRow = el("div", "form-row");
    keyRow.appendChild(el("div", "", "<label>API key</label><div class='hint'>Stored in this browser only.</div>"));
    const keyField = el("input", "field");
    keyField.type = "password";
    keyField.value = state.key;
    keyField.onchange = async () => {
      state.key = keyField.value.trim();
      sessionStorage.setItem("ve_key", state.key);
      try { await refreshStatus(); toast("Key saved"); }
      catch (err) { toast("That key was not accepted", true); }
    };
    keyRow.appendChild(keyField);
    body.appendChild(keyRow);

    card.appendChild(body);
    inner.appendChild(card);
  }

  /* ── theme, full screen, chrome ─────────────────────────────────────── */
  function setTheme(theme) {
    state.theme = theme;
    localStorage.setItem("ve_theme", theme);
    document.documentElement.dataset.theme = theme;
    paintIcon("#theme-toggle", theme === "dark" ? "sun" : "moon");
  }

  function paintIcon(sel, name) {
    const node = $(sel);
    if (node) node.innerHTML = icon(name);
  }

  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
        paintIcon("#fullscreen-toggle", "collapse");
      } else {
        await document.exitFullscreen();
        paintIcon("#fullscreen-toggle", "expand");
      }
    } catch (err) {
      toast("Full screen was refused by the browser", true);
    }
  }

  /* ── wiring ─────────────────────────────────────────────────────────── */
  function wire() {
    // icons that live in the markup
    $$("[data-icon]").forEach((node) => { node.innerHTML = icon(node.dataset.icon); });
    paintIcon("#theme-toggle", state.theme === "dark" ? "sun" : "moon");
    paintIcon("#fullscreen-toggle", "expand");
    paintIcon("#attach-btn", "plus");
    paintIcon("#send-btn", "send");
    paintIcon("#refresh-btn", "refresh");
    $("#rail-toggle").innerHTML = icon("collapse", 16);

    $("#gate-go").onclick = unlock;
    $("#gate-key").addEventListener("keydown", (e) => { if (e.key === "Enter") unlock(); });

    $("#rail-nav").addEventListener("click", (e) => {
      const item = e.target.closest(".nav-item");
      if (item) switchView(item.dataset.view);
    });

    $("#rail-toggle").onclick = () => {
      const app = $("#app");
      if (window.innerWidth <= 860) app.classList.toggle("rail-open");
      else app.classList.toggle("rail-collapsed");
    };

    $("#theme-toggle").onclick = () => setTheme(state.theme === "dark" ? "light" : "dark");
    $("#fullscreen-toggle").onclick = toggleFullscreen;
    document.addEventListener("fullscreenchange", () => {
      paintIcon("#fullscreen-toggle", document.fullscreenElement ? "collapse" : "expand");
    });

    $("#refresh-btn").onclick = async () => { await refreshStatus(); toast("Refreshed"); };
    $("#profile-switch").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      state.profile = b.dataset.profile;
      localStorage.setItem("ve_profile", state.profile);
      $$("#profile-switch button").forEach((x) => x.classList.toggle("is-active", x === b));
    });

    const input = $("#composer-input");
    const send = async () => {
      const text = input.value;
      if (!text.trim()) return;
      input.value = "";
      input.style.height = "auto";
      addUser(text);
      await runCommand(text);
    };
    $("#send-btn").onclick = send;
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(160, input.scrollHeight) + "px";
    });

    $("#attach-btn").onclick = () => $("#file-input").click();
    $("#file-input").addEventListener("change", (e) => {
      const files = Array.from(e.target.files || []);
      if (files.length) uploadFiles(files);
      e.target.value = "";
    });

    // drag and drop anywhere
    const veil = $("#drop-veil");
    let depth = 0;
    window.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; veil.hidden = false; });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) veil.hidden = true; });
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      depth = 0;
      veil.hidden = true;
      const files = Array.from(e.dataTransfer?.files || []);
      if (files.length) uploadFiles(files);
    });
  }

  /* ── boot ───────────────────────────────────────────────────────────── */
  function greet() {
    addSystem({
      iconName: "spark",
      title: "Control Room",
      body: `<p>The engine picks the content, the look, the music and the timing — then renders, checks and queues the video. You never have to choose a template.</p>
             <p class="muted">Try <b>/generate 3</b> for three videos, <b>/status</b> for a summary, or drop a file anywhere to add it to the library.</p>`,
    });
  }

  async function boot() {
    $$("#profile-switch button").forEach((b) => b.classList.toggle("is-active", b.dataset.profile === state.profile));
    greet();
    await refreshStatus();
  }

  // start — the wiring happens either way, so the key gate is usable
  wire();
  setTheme(state.theme);
  if (state.key) {
    $("#gate").hidden = true;
    $("#app").style.display = "";
    boot();
  } else {
    showGate();
  }
})();
