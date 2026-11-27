/*!
 * HGS Communications — Embed SDK v1.1
 * The only file a partner team adds to their application.
 *
 *   <script src="https://messaging.example.com/embed.js"></script>
 *   <script>
 *     const comms = HgsComms.mount('#comms', {
 *       onSent: e => toast(`Sent to ${e.reach} people`)
 *     });
 *   </script>
 *
 * Why an iframe: the HGS API registers no CORS policy, so a partner front-end
 * cannot call it from its own origin. Inside the frame the calls are
 * same-origin and work unchanged. The frame also keeps the customer's contact
 * data, credit balance and auth token out of the host's DOM.
 *
 * AUTH IS OFF in this build. `token` is accepted and forwarded, and the service
 * ignores it. Nothing else about the integration changes when auth lands.
 *
 * No dependencies. ES2017+.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.HgsComms = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var DEFAULT_ORIGIN = typeof location !== 'undefined' ? location.origin : '';
  var WIDGET_PATH = '/embed';

  var EVENTS = [
    'ready',              // booted; payload carries org name, flags, credits, unread count
    'sent',               // a message went out
    'undone',             // pulled back inside the undo window
    'replied',            // the admin answered a reply
    'recipientsChanged',  // audience edited inside the widget
    'viewChanged',        // user switched section
    'tokenExpired',       // host must supply a fresh token
    'error',
    'resize'              // handled for you unless autoResize:false
  ];

  function assign(t) {
    for (var i = 1; i < arguments.length; i++) {
      var s = arguments[i];
      if (s) for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k)) t[k] = s[k];
    }
    return t;
  }

  function resolveTarget(target) {
    if (!target) throw new Error('HgsComms.mount: a target element or selector is required');
    var el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el || el.nodeType !== 1) throw new Error('HgsComms.mount: target "' + target + '" not found');
    return el;
  }

  function Instance(target, options) {
    var opts = assign({
      origin: DEFAULT_ORIGIN,
      widgetPath: WIDGET_PATH,
      token: null,           // forwarded to the frame; ignored while auth is off
      view: 'dashboard',     // which section to open on
      // null = every section the service offers. Set an array to restrict, e.g.
      // ['compose','sent','replies']. Never hard-code the full list here: the
      // widget knows which sections exist and a stale copy silently hides them.
      views: null,
      recipients: null,      // pre-seed the audience
      lockAudience: false,   // host owns the audience; widget shows it read-only
      text: '',
      channels: null,
      theme: 'light',
      tokens: null,          // override an allowlisted set of CSS custom properties
      compact: false,
      showCredits: true,
      allowEmergency: true,
      allowChannelChange: true,
      minHeight: 420,
      autoResize: true,
      title: 'Communications',
      onEvent: null
    }, options);

    EVENTS.forEach(function (name) {
      var cb = 'on' + name.charAt(0).toUpperCase() + name.slice(1);
      if (typeof (options || {})[cb] === 'function') opts[cb] = options[cb];
    });

    this.el = resolveTarget(target);
    this.opts = opts;
    this.destroyed = false;
    this._handlers = {};
    this._queue = [];
    this._ready = false;

    try { this.frameOrigin = new URL(opts.origin, location.href).origin; }
    catch (e) { throw new Error('HgsComms.mount: invalid origin "' + opts.origin + '"'); }

    this._build();
    this._listen();
  }

  Instance.prototype._build = function () {
    var f = document.createElement('iframe');
    f.src = this.opts.origin.replace(/\/+$/, '') + this.opts.widgetPath;
    f.title = this.opts.title;
    // NOT loading="lazy": a deferred frame below the fold never loads, so the
    // handshake never fires and the host is handed a silently empty module.
    f.setAttribute('allow', 'microphone; clipboard-read; clipboard-write');
    f.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms');
    f.setAttribute('referrerpolicy', 'strict-origin');
    f.style.cssText = 'display:block;width:100%;border:0;background:transparent;' +
                      'min-height:' + this.opts.minHeight + 'px;' +
                      'height:' + this.opts.minHeight + 'px;transition:height .18s ease';
    this.frame = f;
    this.el.appendChild(f);
  };

  Instance.prototype._listen = function () {
    var self = this;
    this._onMessage = function (e) {
      if (e.source !== self.frame.contentWindow) return;   // not our frame
      if (e.origin !== self.frameOrigin) return;           // not our origin
      var d = e.data;
      if (!d || d.source !== 'hgs-comms') return;

      if (d.type === 'frameLoaded') { self._init(); return; }

      if (d.type === 'resize') {
        if (self.opts.autoResize) {
          var h = Math.max(self.opts.minHeight, Number(d.payload.height) || 0);
          self.frame.style.height = h + 'px';
        }
        self._emit('resize', d.payload);
        return;
      }

      if (d.type === 'ready') {
        self._ready = true;
        self.info = d.payload;
        while (self._queue.length) self._post.apply(self, self._queue.shift());
      }

      self._emit(d.type, d.payload);
    };
    window.addEventListener('message', this._onMessage);
  };

  Instance.prototype._init = function () {
    this._post('init', {
      token: this.opts.token,
      recipients: this.opts.recipients,
      text: this.opts.text,
      channels: this.opts.channels,
      theme: this.opts.theme,
      tokens: this.opts.tokens,
      view: this.opts.view,
      options: Object.assign({
        compact: this.opts.compact,
        showCredits: this.opts.showCredits,
        allowEmergency: this.opts.allowEmergency,
        allowChannelChange: this.opts.allowChannelChange,
        lockAudience: this.opts.lockAudience,
        autoResize: this.opts.autoResize
      }, Array.isArray(this.opts.views) ? { views: this.opts.views } : null)
    }, true);
  };

  Instance.prototype._post = function (type, payload, force) {
    if (this.destroyed) return;
    if (!this._ready && !force) { this._queue.push([type, payload]); return; }
    this.frame.contentWindow.postMessage(
      { source: 'hgs-comms-host', type: type, payload: payload || {} }, this.frameOrigin);
  };

  Instance.prototype._emit = function (type, payload) {
    var cb = 'on' + type.charAt(0).toUpperCase() + type.slice(1);
    if (typeof this.opts[cb] === 'function') {
      try { this.opts[cb](payload, this); } catch (e) { console.error('[HgsComms] ' + cb, e); }
    }
    if (typeof this.opts.onEvent === 'function') {
      try { this.opts.onEvent(type, payload, this); } catch (e) { console.error('[HgsComms] onEvent', e); }
    }
    (this._handlers[type] || []).forEach(function (fn) {
      try { fn(payload); } catch (e) { console.error('[HgsComms] ' + type, e); }
    });
  };

  /* ---------------------------------------------------------------- public */

  /** Subscribe to an event. Returns an unsubscribe function. */
  Instance.prototype.on = function (type, fn) {
    (this._handlers[type] = this._handlers[type] || []).push(fn);
    var self = this;
    return function () {
      self._handlers[type] = (self._handlers[type] || []).filter(function (f) { return f !== fn; });
    };
  };

  /** Hand the widget a fresh token. Call from your onTokenExpired handler. */
  Instance.prototype.setToken = function (token) {
    this.opts.token = token;
    this._post('setToken', { token: token }, true);
    return this;
  };

  /** Replace the audience. [{kind:'group'|'person', id, name, count}] */
  Instance.prototype.setRecipients = function (recipients) {
    this._post('setRecipients', { recipients: recipients });
    return this;
  };

  /** Pre-fill body and/or channels without touching the audience. */
  Instance.prototype.prefill = function (data) {
    this._post('prefill', data || {});
    return this;
  };

  /** Switch tab: 'compose' | 'sent' | 'replies'. */
  Instance.prototype.show = function (view) {
    this._post('view', { view: view });
    return this;
  };

  /** Clear the composer. */
  Instance.prototype.reset = function () {
    this._post('reset', {});
    return this;
  };

  /** Light/dark, plus optional brand token overrides. */
  Instance.prototype.setTheme = function (theme, tokens) {
    this._post('theme', { theme: theme, tokens: tokens });
    return this;
  };

  /** Remove the frame and detach every listener. */
  Instance.prototype.destroy = function () {
    if (this.destroyed) return;
    this.destroyed = true;
    window.removeEventListener('message', this._onMessage);
    if (this.frame && this.frame.parentNode) this.frame.parentNode.removeChild(this.frame);
    this._handlers = {};
  };

  /* ----------------------------------------------------------------- panel */

  /** Slide-over variant. Returns the same API plus open() and close(). */
  function mountPanel(options) {
    var backdrop = document.createElement('div');
    backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.42);opacity:0;' +
      'transition:opacity .2s;z-index:2147483000;pointer-events:none';

    var panel = document.createElement('div');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-label', (options && options.title) || 'Communications');
    panel.style.cssText = 'position:fixed;top:0;right:0;bottom:0;width:min(440px,100vw);' +
      'background:#fff;box-shadow:-8px 0 28px rgba(0,0,0,.16);transform:translateX(100%);' +
      'transition:transform .22s ease;z-index:2147483001;overflow:hidden';

    document.body.appendChild(backdrop);
    document.body.appendChild(panel);

    var inst = new Instance(panel, assign({}, options, { autoResize: false, minHeight: 0 }));
    inst.frame.style.height = '100%';
    inst.frame.style.minHeight = '100%';

    var lastFocus = null;
    function onKey(e) { if (e.key === 'Escape') inst.close(); }

    inst.open = function () {
      lastFocus = document.activeElement;
      backdrop.style.opacity = '1';
      backdrop.style.pointerEvents = 'auto';
      panel.style.transform = 'translateX(0)';
      document.addEventListener('keydown', onKey);
      return inst;
    };
    inst.close = function () {
      backdrop.style.opacity = '0';
      backdrop.style.pointerEvents = 'none';
      panel.style.transform = 'translateX(100%)';
      document.removeEventListener('keydown', onKey);
      if (lastFocus && lastFocus.focus) lastFocus.focus();
      return inst;
    };
    backdrop.addEventListener('click', function () { inst.close(); });

    var baseDestroy = inst.destroy.bind(inst);
    inst.destroy = function () {
      document.removeEventListener('keydown', onKey);
      baseDestroy();
      [backdrop, panel].forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
    };
    return inst;
  }

  return {
    version: '2.0.0',
    /** Embed inline, inside the element you name. */
    mount: function (target, options) { return new Instance(target, options); },
    /** Embed as a slide-over panel. Call .open() to show it. */
    mountPanel: mountPanel,
    EVENTS: EVENTS.slice()
  };
}));
