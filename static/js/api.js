/*
 * KARMA web UI -- HTTP + SSE client and small DOM helpers.
 *
 * Everything the views need to talk to the backend lives here so view code
 * stays declarative. `KARMA.api` wraps fetch with JSON handling and error
 * propagation; `KARMA.api.stream` wraps EventSource with an auto-reconnect
 * poll fallback for environments where SSE is unavailable. `el` and `clear`
 * are minimal DOM helpers used in place of a framework.
 */
(function () {
  "use strict";

  const KARMA = (window.KARMA = window.KARMA || {});

  async function request(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(path, opts);
    const text = await resp.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_e) {
      data = { raw: text };
    }
    if (!resp.ok) {
      const msg = (data && data.error) || resp.statusText || "request failed";
      const err = new Error(msg);
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  // The UI shows one live stream at a time. Track active stream handles so a
  // new stream (or a view switch) can close stale ones -- otherwise old
  // EventSources keep firing into the page and garble it (interleaved logs,
  // cross-view toasts, appends to detached nodes).
  const _streams = new Set();
  function closeAllStreams() {
    for (const h of [..._streams]) { try { h.close(); } catch (_e) { /* ignore */ } }
    _streams.clear();
  }

  KARMA.api = {
    closeAllStreams,
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),

    /*
     * Subscribe to an SSE endpoint. Calls onEvent(obj) for each JSON event
     * and onDone() on the terminal {type:"done"} event or stream end.
     * Returns a handle with .close(). Falls back to polling `statusPath`
     * (if given) when EventSource is missing or errors immediately.
     */
    stream(path, { onEvent, onDone, statusPath, pollMs = 2000 } = {}) {
      closeAllStreams();   // only one live stream in the UI at a time
      let closed = false;
      let pollTimer = null;

      function startPolling() {
        if (!statusPath) return;
        async function tick() {
          if (closed) return;
          try {
            const status = await request("GET", statusPath);
            onEvent && onEvent({ type: "status", status });
            const s = status && status.status;
            if (s && ["complete", "failed", "error", "cancelled"].includes(s)) {
              onDone && onDone();
              return;
            }
          } catch (_e) {
            /* keep polling */
          }
          pollTimer = setTimeout(tick, pollMs);
        }
        tick();
      }

      if (typeof EventSource === "undefined") {
        startPolling();
        const handle = { close() { closed = true; clearTimeout(pollTimer); _streams.delete(handle); } };
        _streams.add(handle);
        return handle;
      }

      const es = new EventSource(path);
      let gotAny = false;
      let finished = false;
      function finish() {
        if (finished) return;
        finished = true;
        es.close();
        onDone && onDone();
      }
      es.onmessage = (e) => {
        gotAny = true;
        let obj;
        try { obj = JSON.parse(e.data); } catch (_e) { return; }
        if (obj && obj.type === "done") { finish(); return; }
        onEvent && onEvent(obj);
      };
      es.onerror = () => {
        // EventSource fires onerror both on a transient drop (it would
        // auto-reconnect) and when the server closes the stream after the
        // terminal event. We only treat it as terminal once: if SSE never
        // delivered anything, fall back to polling; otherwise finish once.
        if (finished) return;
        if (!gotAny) { es.close(); startPolling(); }
        else finish();
      };
      const handle = { close() { closed = true; es.close(); clearTimeout(pollTimer); _streams.delete(handle); } };
      _streams.add(handle);
      return handle;
    },
  };

  // --- tiny DOM helpers -----------------------------------------------------
  KARMA.el = function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") node.className = v;
        else if (k === "html") node.innerHTML = v;
        else if (k.startsWith("on") && typeof v === "function") {
          node.addEventListener(k.slice(2).toLowerCase(), v);
        } else if (v !== null && v !== undefined && v !== false) {
          node.setAttribute(k, v);
        }
      }
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  };

  KARMA.clear = function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  };

  KARMA.escape = function escape(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  };
})();
