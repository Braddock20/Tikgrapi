/* ==========================================================================
   TikTok Engine — control room
   No framework, no build step. One file, one job.
   ========================================================================== */
(() => {
  'use strict';

  const $  = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const state = {
    key: localStorage.getItem('tiktok.engine.key') || '',
    commands: {},
    categories: {},
    view: 'console',
    history: [],
    historyAt: -1,
    sending: false,
  };

  /* ---------------------------------------------------------------- utils */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const timeOf = (d = new Date()) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  function toast(text, kind = '') {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.textContent = text;
    $('#toasts').appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 320);
    }, 2600);
  }

  async function api(path, options = {}) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (state.key) opts.headers['X-API-Key'] = state.key;
    if (opts.body && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      // On the first load there is no key yet — a quiet nudge is kinder than
      // a dialog that covers the screen before anything has happened.
      if (opts.quiet) {
        toast('add your API key to use the control room', 'bad');
      } else {
        openKeyDialog();
      }
      throw new Error('the API key was refused');
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!res.ok) {
      const detail = (data && (data.detail || data.error)) || res.statusText;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  }

  /* ---------------------------------------------------------------- theme */
  const savedTheme = localStorage.getItem('tiktok.engine.theme');
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  else document.documentElement.dataset.theme =
    window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';

  $('#themeBtn').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('tiktok.engine.theme', next);
  });

  /* -------------------------------------------------------------- routing */
  function showView(name) {
    if (state.view === name) return;
    const swap = () => {
      state.view = name;
      $$('.tab').forEach((t) => t.classList.toggle('is-active', t.dataset.view === name));
      $$('.bnav').forEach((t) => t.classList.toggle('is-active', t.dataset.view === name));
      $$('.view').forEach((v) => v.classList.toggle('is-active', v.id === `view-${name}`));
      if (name === 'status') refreshStatus();
      if (name === 'automations') refreshAutomations();
      if (name === 'commands') renderCommands();
      window.scrollTo({ top: 0, behavior: 'instant' });
    };

    if (document.startViewTransition) {
      document.startViewTransition(swap);
    } else {
      swap();
    }
    history.replaceState(null, '', `#${name}`);
  }

  $$('.tab, .bnav').forEach((btn) => {
    btn.addEventListener('click', () => showView(btn.dataset.view));
  });

  $('#menuBtn').addEventListener('click', (e) => {
    const tabs = $('#tabs');
    const open = tabs.classList.toggle('only-mobile');
    e.currentTarget.setAttribute('aria-expanded', String(open));
  });

  const initial = (location.hash || '').replace('#', '');
  if (['console', 'commands', 'automations', 'status'].includes(initial)) {
    setTimeout(() => showView(initial), 0);
  }

  /* ------------------------------------------------------------ key modal */
  const keyModal = $('#keyModal');
  function openKeyDialog() {
    $('#keyInput').value = state.key;
    if (typeof keyModal.showModal === 'function') keyModal.showModal();
    else keyModal.setAttribute('open', '');
    $('#keyInput').focus();
  }
  $('#keyBtn').addEventListener('click', openKeyDialog);
  $('#keySave').addEventListener('click', () => {
    state.key = $('#keyInput').value.trim();
    localStorage.setItem('tiktok.engine.key', state.key);
    toast(state.key ? 'the key was saved' : 'the key was cleared', 'good');
    refreshAll();
  });

  /* ---------------------------------------------------------------- chat */
  const chat = $('#chat');
  const input = $('#commandInput');

  function addMessage(role, html, opts = {}) {
    const wrap = document.createElement('div');
    wrap.className = `msg ${role}${opts.error ? ' err' : ''}`;
    wrap.innerHTML = `
      <div class="msg-avatar">${role === 'me' ? '›' : '✦'}</div>
      <div class="msg-body">
        ${html}
        <div class="msg-time">${timeOf()}</div>
      </div>`;
    chat.appendChild(wrap);
    chat.scrollTop = chat.scrollHeight;
    return wrap;
  }

  function renderReply(text) {
    const escaped = esc(text);
    const html = escaped
      .split(/\n{2,}/)
      .map((block) => {
        if (block.includes('\n') && !block.startsWith('<')) {
          return `<pre>${esc(block)}</pre>`;
        }
        return `<p>${esc(block).replace(/\n/g, '<br>')}</p>`;
      })
      .join('');
    return html;
  }

  async function runCommand(raw) {
    const text = (raw || '').trim();
    if (!text || state.sending) return;
    state.sending = true;
    $('.send-btn').disabled = true;

    const cmd = text.startsWith('/') ? text : `/${text}`;
    addMessage('me', `<p>${esc(cmd)}</p>`);

    const typing = addMessage('bot', '<p class="typing"><i></i><i></i><i></i></p>');
    try {
      const data = await api('/api/chat', { method: 'POST', body: { command: cmd } });
      typing.remove();
      const reply = data.reply || '(nothing to say)';
      addMessage('bot', renderReply(reply), { error: /failed|error|not available|refused/i.test(reply) });
    } catch (err) {
      typing.remove();
      addMessage('bot', `<p>${esc(err.message)}</p>`, { error: true });
    } finally {
      state.sending = false;
      $('.send-btn').disabled = false;
      input.focus();
    }
  }

  $('#composer').addEventListener('submit', (e) => {
    e.preventDefault();
    const value = input.value;
    input.value = '';
    state.history.unshift(value);
    state.historyAt = -1;
    localStorage.setItem('tiktok.engine.history', JSON.stringify(state.history.slice(0, 60)));
    runCommand(value);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (state.historyAt < state.history.length - 1) {
        state.historyAt += 1;
        input.value = state.history[state.historyAt] || '';
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (state.historyAt > 0) {
        state.historyAt -= 1;
        input.value = state.history[state.historyAt] || '';
      } else {
        state.historyAt = -1;
        input.value = '';
      }
    }
  });

  try {
    state.history = JSON.parse(localStorage.getItem('tiktok.engine.history') || '[]');
  } catch { state.history = []; }

  /* ---------------------------------------------------- command catalogue */
  const QUICK = ['/help', '/status', '/myvideos', '/notifications', '/explore', '/memory', '/queue'];

  function renderQuickChips() {
    const host = $('#quickChips');
    host.innerHTML = QUICK.map(
      (c) => `<button class="chip" data-cmd="${esc(c)}">${esc(c)}</button>`,
    ).join('');
    host.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', () => runCommand(chip.dataset.cmd));
    });
  }

  function renderCommands() {
    const host = $('#catList');
    const query = ($('#cmdSearch').value || '').trim().toLowerCase();
    const groups = state.commands;
    const keys = Object.keys(groups);

    if (!keys.length) {
      host.innerHTML = '<div class="empty">The command list has not loaded yet.</div>';
      return;
    }

    let shown = 0;
    const parts = [];
    for (const key of keys) {
      const items = groups[key].filter((c) => {
        if (!query) return true;
        return `${c.command} ${c.usage} ${c.summary} ${(c.aliases || []).join(' ')}`
          .toLowerCase().includes(query);
      });
      if (!items.length) continue;
      shown += items.length;
      parts.push(`<section class="cat">
        <h3 class="cat-head">${esc(state.categories[key] || key)}</h3>
        <div class="cmd-grid">${items.map((c) => `
          <button class="cmd" data-cmd="${esc(c.usage.split(' ')[0])}">
            <span class="c-usage">${esc(c.usage)}${c.write ? '<span class="badge">write</span>' : ''}</span>
            <span class="c-sum">${esc(c.summary)}</span>
          </button>`).join('')}</div>
      </section>`);
    }

    host.innerHTML = shown
      ? parts.join('')
      : '<div class="empty">Nothing matches that search.</div>';
    $('#cmdCount').textContent = `${shown} command${shown === 1 ? '' : 's'}`;

    host.querySelectorAll('.cmd').forEach((el) => {
      el.addEventListener('click', () => {
        const cmd = el.dataset.cmd;
        const item = Object.values(groups).flat().find((c) => c.usage.split(' ')[0] === cmd);
        input.value = cmd;
        showView('console');
        if (item && item.usage.includes(' ')) input.focus();
      });
    });
  }

  $('#cmdSearch').addEventListener('input', renderCommands);

  /* ---------------------------------------------------------- automations */
  async function refreshAutomations(quiet = false) {
    try {
      const [jobs, aliases] = await Promise.all([
        api('/api/scheduler', { quiet }),
        api('/api/aliases', { quiet }),
      ]);
      renderJobs(jobs.jobs || []);
      renderAliases(aliases.aliases || []);
    } catch (err) {
      $('#jobList').innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    }
  }

  function renderJobs(jobs) {
    const host = $('#jobList');
    if (!jobs.length) {
      host.innerHTML = '<div class="empty">No scheduled jobs yet. Add one above.</div>';
      return;
    }
    host.innerHTML = jobs.map((j) => `
      <div class="list-item">
        <div class="li-main">
          <div class="li-title">${esc(j.schedule)}</div>
          <div class="li-sub">${esc(j.command)}</div>
        </div>
        <button class="li-x" data-del="${esc(j.id)}" aria-label="Remove the job">×</button>
      </div>`).join('');
    host.querySelectorAll('[data-del]').forEach((b) => {
      b.addEventListener('click', async () => {
        try {
          await api(`/api/scheduler/${encodeURIComponent(b.dataset.del)}`, { method: 'DELETE' });
          toast('the job was removed', 'good');
          refreshAutomations();
        } catch (err) { toast(err.message, 'bad'); }
      });
    });
  }

  function renderAliases(names) {
    const host = $('#aliasList');
    if (!names.length) {
      host.innerHTML = '<div class="empty">No custom commands yet. Add one above.</div>';
      return;
    }
    host.innerHTML = names.map((n) => `
      <div class="list-item">
        <div class="li-main">
          <div class="li-title">/${esc(n)}</div>
          <div class="li-sub">${esc((state.aliasMap || {})[n] || '')}</div>
        </div>
        <button class="li-x" data-del="${esc(n)}" aria-label="Remove the alias">×</button>
      </div>`).join('');
    host.querySelectorAll('[data-del]').forEach((b) => {
      b.addEventListener('click', async () => {
        try {
          await api(`/api/aliases/${encodeURIComponent(b.dataset.del)}`, { method: 'DELETE' });
          toast('the alias was removed', 'good');
          refreshAutomations();
        } catch (err) { toast(err.message, 'bad'); }
      });
    });
  }

  $('#jobForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const schedule = $('#jobSchedule').value.trim();
    const command = $('#jobCommand').value.trim();
    if (!schedule || !command) { toast('a schedule and a command are needed', 'bad'); return; }
    try {
      await api('/api/scheduler', { method: 'POST', body: { schedule, command } });
      toast('the job was scheduled', 'good');
      $('#jobSchedule').value = ''; $('#jobCommand').value = '';
      refreshAutomations();
    } catch (err) { toast(err.message, 'bad'); }
  });

  $('#aliasForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = $('#aliasName').value.trim();
    const command = $('#aliasCommand').value.trim();
    if (!name || !command) { toast('a name and a command are needed', 'bad'); return; }
    try {
      await api('/api/aliases', { method: 'POST', body: { name, command } });
      toast(`/${name} now runs ${command}`, 'good');
      $('#aliasName').value = ''; $('#aliasCommand').value = '';
      refreshAutomations();
    } catch (err) { toast(err.message, 'bad'); }
  });

  /* ---------------------------------------------------------------- status */
  async function refreshStatus(quiet = false) {
    try {
      const health = await api('/health', { quiet });
      renderSession(health);
      renderMemory(health);
    } catch (err) {
      $('#sessionStats').innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    }
    try {
      const ops = await api('/api/operations', { quiet });
      const host = $('#opList');
      host.innerHTML = ops.operations.map((o) => `
        <div class="op">
          <span class="o-name">${esc(o.operation)}</span>
          <span class="o-sum">${esc(o.summary)}</span>
          <span class="o-tag">${esc(o.backend)}</span>
        </div>`).join('');
    } catch { /* the session may not be up yet */ }
  }

  function renderSession(health) {
    const s = health.session || {};
    const status = s.status || 'unknown';
    const attached = s.attached === true;
    const cls = status === 'valid' ? 'good' : (status === 'missing' ? 'warn' : 'warn');
    const cards = [
      { label: 'Session', value: status, cls },
      { label: 'Attached', value: attached ? 'yes' : 'no', cls: attached ? 'good' : 'warn' },
      { label: 'Writes', value: health.write_ops_enabled ? 'on' : 'off',
        cls: health.write_ops_enabled ? 'good' : 'warn' },
      { label: 'Paused', value: health.writes_paused ? 'yes' : 'no',
        cls: health.writes_paused ? 'warn' : 'good' },
      { label: 'Renewals', value: s.renewals ?? 0 },
      { label: 'Heals', value: s.heals ?? 0 },
    ];
    if (s.sessionid) cards.push({ label: 'Session id', value: s.sessionid, sub: 'masked' });
    if (health.uptime_seconds != null) {
      const up = Math.floor(health.uptime_seconds);
      cards.push({ label: 'Uptime', value: `${Math.floor(up / 60)}m ${up % 60}s` });
    }
    $('#sessionStats').innerHTML = cards.map((c) => `
      <div class="stat">
        <div class="s-label">${esc(c.label)}</div>
        <div class="s-value ${c.cls || ''}">${esc(c.value)}</div>
        ${c.sub ? `<div class="s-sub">${esc(c.sub)}</div>` : ''}
      </div>`).join('');

    const bot = health.bot || {};
    $('#brandSub').textContent = bot.running
      ? `bot on · ${bot.channel || 'chat'}`
      : (status === 'valid' ? 'session attached' : 'session ' + status);
  }

  function renderMemory(health) {
    const m = health.memory || {};
    const cards = [
      { label: 'Process', value: `${m.rss_mb ?? '?'} MB`, sub: 'resident memory' },
      { label: 'System', value: m.used_mb != null ? `${m.used_mb} MB` : '?',
        sub: m.total_mb ? `of ${m.total_mb} MB` : '' },
      { label: 'Free', value: m.free_mb != null ? `${m.free_mb} MB` : '?' },
      { label: 'Disk', value: m.disk_free_mb != null ? `${m.disk_free_mb} MB` : '?' },
    ];
    const browser = (health.bot && health.bot.browser) || {};
    if (browser.running !== undefined) {
      cards.push({ label: 'Browser', value: browser.running ? 'up' : 'idle',
        cls: browser.running ? 'warn' : 'good',
        sub: browser.starts ? `${browser.starts} start(s)` : 'released when idle' });
    }
    $('#memStats').innerHTML = cards.map((c) => `
      <div class="stat">
        <div class="s-label">${esc(c.label)}</div>
        <div class="s-value ${c.cls || ''}">${esc(c.value)}</div>
        ${c.sub ? `<div class="s-sub">${esc(c.sub)}</div>` : ''}
      </div>`).join('');

    const meter = $('#memMeter');
    if (m.percent_used != null) {
      meter.hidden = false;
      $('.meter-fill', meter).style.width = `${Math.min(100, m.percent_used)}%`;
    }
  }

  $('#renewBtn').addEventListener('click', async () => {
    try {
      await api('/admin/session/renew', { method: 'POST' });
      toast('the session was renewed', 'good');
      refreshStatus();
    } catch (err) { toast(err.message, 'bad'); }
  });

  $('#welcomeBtn').addEventListener('click', async () => {
    try {
      const res = await api('/api/welcome', { method: 'POST' });
      if (res && res.sent) {
        toast(res.detail || 'the welcome message was sent', 'good');
      } else {
        const why = (res && (res.detail || res.hint)) || 'TikTok refused the message';
        toast(why, 'bad');
      }
    } catch (err) { toast(err.message, 'bad'); }
  });

  const pauseBtn = $('#pauseBtn');
  pauseBtn.addEventListener('click', async () => {
    try {
      await api('/admin/pause', { method: 'POST' });
      toast('writes are paused', 'good');
      refreshStatus();
    } catch (err) { toast(err.message, 'bad'); }
  });
  $('#resumeBtn').addEventListener('click', async () => {
    try {
      await api('/admin/resume', { method: 'POST' });
      toast('writes are resumed', 'good');
      refreshStatus();
    } catch (err) { toast(err.message, 'bad'); }
  });

  /* --------------------------------------------------------------- loading */
  async function loadCommands(quiet = false) {
    try {
      const data = await api('/api/commands', { quiet });
      state.commands = data.commands || {};
      state.categories = data.categories || {};
      renderCommands();
    } catch (err) {
      $('#cmdCount').textContent = err.message;
    }
  }

  async function refreshAll(quiet = false) {
    await Promise.allSettled([
      loadCommands(quiet),
      refreshStatus(quiet),
      refreshAutomations(quiet),
    ]);
  }

  renderQuickChips();
  refreshAll(!state.key);   // the first load is quiet when there is no key yet

  /* Keep the status panel honest without hammering the API. */
  setInterval(() => {
    if (state.view === 'status') refreshStatus();
  }, 15000);

  window.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      showView('commands');
      $('#cmdSearch').focus();
    }
  });
})();
