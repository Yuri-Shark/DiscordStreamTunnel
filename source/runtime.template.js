(() => {
  'use strict';
  const config = /*__CONFIG__*/null;
  const key = '__discordProtonStreams';
  if (window[key]) return window[key].status();
  const W = Vencord.Webpack, store = W.findStore('StreamRTCConnectionStore');
  if (!store) throw new Error('Stream connection store unavailable');
  const wrapped = new Map();
  const watchedRoots = new Map();
  let enabled = true, automatic = false, constructions = 0, lastError = null;
  let videoGuard = 'waiting', guardAttempts = 0;
  function applyVideoGuard() {
    if (!enabled || videoGuard === 'enabled' || videoGuard === 'unsupported') return;
    if (typeof W.find !== 'function') {videoGuard = 'unsupported'; return;}
    const id = '2026-08-video-guard';
    try {
      const experiment = W.find(x => x?.definition?.name === id, {isIndirect:true});
      const apex = W.findStore('ApexExperimentStore');
      if (experiment?.definition?.defaultConfig?.videoEnabled === true && apex?.getState) {
        const before = apex.getState().clientOverrides?.[id]?.variantId ?? null;
        window.DiscordProtonStreamsNative?.rememberVideoGuard?.(before);
        if (before !== 0) W.Common.FluxDispatcher.dispatch({type:'APEX_EXPERIMENT_OVERRIDE_CREATE',experimentName:id,variantId:0});
        apex.persist?.();
        videoGuard = apex.getState().clientOverrides?.[id]?.variantId === 0 ? 'enabled' : 'unavailable';
        return;
      }
    } catch {videoGuard = 'unavailable'; return;}
    if (++guardAttempts < 60) setTimeout(applyVideoGuard, 500);
    else videoGuard = 'unavailable';
  }
  const base = `ws://127.0.0.1:${config.relayPort}/relay`;
  const portAllowed = new Set(['', '443', '2053', '2083', '2087', '2096', '8443']);
  function allowed(target) {
    try {
      const u = new URL(target);
      return u.protocol === 'wss:' && /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.discord\.media$/.test(u.hostname)
        && portAllowed.has(u.port) && ['', '/'].includes(u.pathname) && !u.username && !u.password && !u.hash
        && [...u.searchParams.keys()].length === 1 && ['9', '10'].includes(u.searchParams.get('v'));
    } catch { return false; }
  }
  function attach(root) {
    if (root.context !== 'stream') return;
    const socket = root._socket;
    if (!socket || wrapped.has(socket) || typeof socket.createWebSocket !== 'function') return;
    const original = socket.createWebSocket;
    const record = {original, root, session: crypto.randomUUID().replaceAll('-', ''), replacement: null, renewed: false,retries:0,retryAt:0,
      ready: typeof window.DiscordProtonStreamsNative?.ensureHelper !== 'function', starting: false};
    record.replacement = function (...factoryArgs) {
      if(enabled&&automatic&&typeof window.DiscordProtonStreamsNative?.ensureHelper==='function'){
        record.pendingFactory={self:this,args:factoryArgs};record.ready=false;scan();return;
      }
      if (!enabled || !record.ready) return original.apply(this, factoryArgs);
      return record.open.apply(this,factoryArgs);
    };
    record.open = function (...factoryArgs) {
      const previous = window.WebSocket, expected = new URL(this.url).origin;
      const StreamSocket = new Proxy(previous, {
        construct(Target, args) {
          const url = String(args[0]), next = [...args];
          if (allowed(url) && new URL(url).origin === expected) {
            next[0] = base + '?' + new URLSearchParams({nonce: config.nonce, target: url, sid: record.session});
            record.renewed = true;
            constructions++;
          }
          return Reflect.construct(Target, next, Target);
        }
      });
      window.WebSocket = StreamSocket;
      try { return original.apply(this, factoryArgs); }
      finally { window.WebSocket = previous; }
    };
    socket.createWebSocket = record.replacement;
    wrapped.set(socket, record);
  }
  function watchRoot(root) {
    if (root.context !== 'stream' || watchedRoots.has(root)) return;
    const descriptor = Object.getOwnPropertyDescriptor(root, '_socket');
    if (descriptor && (!descriptor.configurable || !descriptor.writable || !('value' in descriptor))) return;
    const record = {descriptor, socket: root._socket};
    record.get = () => record.socket;
    record.set = socket => {
      record.socket = socket;
      // Native connect() assigns the socket and opens it in the same call.
      // Attach before its first WebSocket, including for stream senders.
      try { attach(root); } catch (error) { lastError = error?.name ?? 'RuntimeError'; }
    };
    Object.defineProperty(root, '_socket', {get: record.get, set: record.set,
      enumerable: descriptor?.enumerable ?? true, configurable: true});
    watchedRoots.set(root, record);
  }
  function unwatchRoot(root, record) {
    const current = Object.getOwnPropertyDescriptor(root, '_socket');
    if (current?.get === record.get && current?.set === record.set) {
      Object.defineProperty(root, '_socket', record.descriptor
        ? {...record.descriptor, value: record.socket}
        : {value: record.socket, writable: true, enumerable: true, configurable: true});
    }
    watchedRoots.delete(root);
  }
  function scan() {
    if (!enabled) return;
    try {
      const roots = Object.values(store.getRTCConnections());
      for (const [root, record] of watchedRoots) if (!roots.includes(root)) unwatchRoot(root, record);
      for (const root of roots) watchRoot(root);
      const activeSockets = new Set(roots.map(x => x._socket));
      for (const [socket, record] of wrapped) {
        if (!activeSockets.has(socket)) {
          if (socket.createWebSocket === record.replacement) socket.createWebSocket = record.original;
          wrapped.delete(socket);
        }
      }
      for (const root of roots) {
        attach(root);
        const socket = root._socket, record = wrapped.get(socket);
        if (automatic && record && !record.ready && !record.starting && Date.now()>=record.retryAt) {
          record.starting = true;
          Promise.resolve(window.DiscordProtonStreamsNative.ensureHelper()).then(() => {
            record.ready = true; record.starting = false;record.retries=0;record.retryAt=0;lastError=null;
            if(record.pendingFactory){const pending=record.pendingFactory;record.pendingFactory=null;if(enabled&&automatic&&wrapped.get(socket)===record&&root._socket===socket)record.open.apply(pending.self,pending.args);}
            if (enabled && automatic) scan();
          }).catch(() => {record.starting = false;lastError='HelperUnavailable';record.retryAt=Date.now()+Math.min(5000,250*2**record.retries++);if(enabled&&automatic)setTimeout(scan,Math.max(0,record.retryAt-Date.now()));});
        }
        if (automatic && record && record.ready && !record.renewed && !root.isStreamSender() && root.state === 'RTC_CONNECTED' && !socket.webSocket?.url?.startsWith(base)) {
          record.renewed = true;
          setTimeout(() => {
            if (enabled && automatic && Object.values(store.getRTCConnections()).includes(root) && root._socket === socket) root.reconnect();
          }, 0);
        }
      }
    } catch (error) { lastError = error?.name ?? 'RuntimeError'; }
  }
  function status() {
    const roots = Object.values(store.getRTCConnections()).filter(x => x.context === 'stream');
    return {enabled, automatic, constructions, wrappedSockets: wrapped.size, lastError, videoGuard,
      streams: roots.map(x => ({state: x.state, sender: x.isStreamSender(), throughRelay: !!x._socket?.webSocket?.url?.startsWith(base)})),
      scope: 'Somente sessoes de compartilhamento de tela; voz principal e demais aplicativos permanecem diretos.'};
  }
  function testReceivers() {
    scan();
    const roots = Object.values(store.getRTCConnections()).filter(x => x.context === 'stream' && !x.isStreamSender());
    if (!roots.length) throw new Error('Nenhuma recepcao de tela aberta');
    for (const root of roots) {
      if (!wrapped.has(root._socket)) throw new Error('Controle da recepcao indisponivel');
      root.reconnect();
      const q = root._goLiveQualityManager;
      if (q?.userId) W.Common.FluxDispatcher.dispatch({type:'CLEAR_VIDEO_STREAM_READY_TIMEOUT',mediaContext:'stream',userId:q.userId});
    }
    return status();
  }
  function remove(options = {}) {
    enabled = false; automatic = false; store.removeChangeListener(scan);
    for (const [socket, record] of wrapped) if (socket.createWebSocket === record.replacement) socket.createWebSocket = record.original;
    for (const [root, record] of watchedRoots) unwatchRoot(root, record);
    wrapped.clear(); delete window[key];
    if (options.reconnectReceivers) for (const root of Object.values(store.getRTCConnections())) {
      if (root.context === 'stream' && !root.isStreamSender()) root.reconnect();
    }
    return {removed: true};
  }
  store.addChangeListener(scan);
  window[key] = {status, testarRecepcaoAtual: testReceivers, automatico(value = true) {automatic = !!value; scan(); return status();}, remover: remove};
  applyVideoGuard(); scan(); return status();
})()
