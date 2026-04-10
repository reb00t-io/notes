// Client bridge WebSocket + console/log capture.
//
// Opens a WebSocket to /v1/bridge?session_id=... and exposes a ring buffer
// of recent logs the backend can pull via get_client_logs.

const LOG_BUFFER_MAX = 500;

export class ClientBridge {
  constructor() {
    this.sessionId = null;
    this.ws = null;
    this.logs = [];
    this._installLogCapture();
  }

  _installLogCapture() {
    for (const level of ['log', 'info', 'warn', 'error']) {
      const original = console[level].bind(console);
      console[level] = (...args) => {
        this._push(level, args.map(a => this._stringify(a)).join(' '));
        original(...args);
      };
    }
    window.addEventListener('error', (e) => {
      this._push('error', e?.message || String(e?.error || 'error'));
    }, true);
    window.addEventListener('unhandledrejection', (e) => {
      this._push('error', `unhandledrejection: ${e?.reason}`);
    });

    const origFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const url = typeof args[0] === 'string' ? args[0] : args[0]?.url;
      try {
        const r = await origFetch(...args);
        if (!r.ok) this._push('warn', `fetch ${r.status} ${r.url || url}`);
        return r;
      } catch (err) {
        this._push('error', `fetch failed ${url}: ${err?.message || err}`);
        throw err;
      }
    };
  }

  _stringify(v) {
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v); } catch { return String(v); }
  }

  _push(level, message) {
    this.logs.push({ level, message, ts: new Date().toISOString() });
    if (this.logs.length > LOG_BUFFER_MAX) {
      this.logs.splice(0, this.logs.length - LOG_BUFFER_MAX);
    }
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify({ type: 'log', entry: { level, message } }));
      } catch { /* noop */ }
    }
  }

  connect(sessionId) {
    if (this.sessionId === sessionId && this.ws?.readyState === WebSocket.OPEN) return;
    this.sessionId = sessionId;
    if (this.ws) { try { this.ws.close(); } catch {} }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/v1/bridge?session_id=${encodeURIComponent(sessionId)}`;
    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      console.warn('bridge ws failed to open', err);
      return;
    }

    this.ws.addEventListener('message', (ev) => this._onMessage(ev));
    this.ws.addEventListener('close', () => {
      if (this.sessionId) setTimeout(() => this.connect(this.sessionId), 2000);
    });
  }

  _onMessage(ev) {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg?.type !== 'call') return;
    const { id, name, args } = msg;
    Promise.resolve(this._handleCall(name, args || {}))
      .catch(err => ({ error: String(err?.message || err) }))
      .then(result => {
        this.ws.send(JSON.stringify({ type: 'result', id, result }));
      });
  }

  async _handleCall(name, args) {
    switch (name) {
      case 'dom_query': return this._domQuery(args);
      case 'dom_eval': return this._domEval(args);
      case 'dom_patch': return this._domPatch(args);
      case 'reload_page': return this._reload();
      default: return { error: `unknown tool: ${name}` };
    }
  }

  _domQuery({ selector, limit = 10 }) {
    const iframe = document.getElementById('sheet-frame');
    const doc = iframe?.contentDocument;
    if (!doc) return { error: 'no_open_page' };
    const elements = [...doc.querySelectorAll(selector)].slice(0, limit);
    return {
      matches: elements.map(el => ({
        tag: el.tagName.toLowerCase(),
        text: (el.textContent || '').slice(0, 200),
        attrs: Object.fromEntries(
          [...el.attributes].map(a => [a.name, a.value]).slice(0, 20)
        ),
      })),
    };
  }

  _domEval({ js }) {
    const iframe = document.getElementById('sheet-frame');
    const win = iframe?.contentWindow;
    if (!win) return { error: 'no_open_page' };
    try {
      // eslint-disable-next-line no-new-func
      const fn = new win.Function(js);
      const result = fn();
      return { result: JSON.parse(JSON.stringify(result ?? null)) };
    } catch (err) {
      return { error: String(err?.message || err) };
    }
  }

  _domPatch({ selector, action, value, attr }) {
    const iframe = document.getElementById('sheet-frame');
    const doc = iframe?.contentDocument;
    if (!doc) return { error: 'no_open_page' };
    const el = doc.querySelector(selector);
    if (!el) return { error: 'no_match' };
    try {
      switch (action) {
        case 'set_text': el.textContent = value; break;
        case 'set_html': el.innerHTML = value; break;
        case 'set_attr':
          if (!attr) return { error: 'missing attr' };
          el.setAttribute(attr, value);
          break;
        case 'add_class': el.classList.add(value); break;
        case 'remove_class': el.classList.remove(value); break;
        default: return { error: `unknown action: ${action}` };
      }
      return { ok: true };
    } catch (err) {
      return { error: String(err?.message || err) };
    }
  }

  _reload() {
    const iframe = document.getElementById('sheet-frame');
    if (iframe?.src) {
      const cur = iframe.src;
      iframe.src = 'about:blank';
      setTimeout(() => { iframe.src = cur; }, 50);
    }
    return { ok: true };
  }
}

export const bridge = new ClientBridge();
