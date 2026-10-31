(() => {
  'use strict';
  const config = /*__CONFIG__*/null;
  const key = '__discordProtonStreams';
  if (window[key]) return window[key].status();
  const W = Vencord.Webpack;
  function loaded(predicate) {
    // A lazy export or a throwing getter must not hide the remaining exports.
    for (const mod of Object.values(W.cache || {})) {
      let exports; try { exports = mod.exports; if (predicate(exports)) return exports; } catch { continue; }
      let names; try { names = Object.keys(exports || {}); } catch { continue; }
      for (const name of names) try { const value = exports[name]; if (predicate(value)) return value; } catch {}
    }
    return null;
  }
  function findStore(name, usable) {
    const matches = value => value?.constructor?.displayName === name && usable(value);
    try { const value = W.fluxStores?.get(name); if (usable(value)) return value; } catch {}
    const cached = loaded(matches); if (cached) return cached;
    try { const value = W.findStore(name); if (usable(value)) return value; } catch {}
    return null;
  }
  const store = findStore('StreamRTCConnectionStore', value => value && typeof value.getRTCConnections === 'function'
    && typeof value.addChangeListener === 'function' && typeof value.removeChangeListener === 'function');
  if (!store) throw new Error('Stream connection store unavailable');
  const wrapped = new Map();
  const watchedRoots = new Map();
  let enabled = true, automatic = false, constructions = 0, lastError = null;
  let videoGuard = 'waiting', guardAttempts = 0;
  function applyVideoGuard() {
    if (!enabled || videoGuard === 'enabled' || videoGuard === 'unsupported') return;
    const id = '2026-08-video-guard';
    try {
      const match = x => x?.definition?.name === id;
      const experiment = loaded(match) || (typeof W.find === 'function' ? W.find(match, {isIndirect:true}) : null);
      const apex = findStore('ApexExperimentStore', value => value && typeof value.getState === 'function');
      if (experiment?.definition?.defaultConfig?.videoEnabled === true && apex?.getState) {
        const before = apex.getState().clientOverrides?.[id]?.variantId ?? null;
        const usable = value => value && typeof value.dispatch === 'function' && typeof value.subscribe === 'function';
        let dispatcher; try { dispatcher = W.Common?.FluxDispatcher; if (typeof dispatcher?.dispatch !== 'function') dispatcher = null; } catch {}
        dispatcher ||= loaded(usable);
        if (before !== 0 && !dispatcher) throw new Error('VideoDispatcherUnavailable');
        window.DiscordProtonStreamsNative?.rememberVideoGuard?.(before);
        if (before !== 0) dispatcher.dispatch({type:'APEX_EXPERIMENT_OVERRIDE_CREATE',experimentName:id,variantId:0});
        apex.persist?.();
        videoGuard = apex.getState().clientOverrides?.[id]?.variantId === 0 ? 'enabled' : 'unavailable';
        if (videoGuard === 'enabled') return;
      }
    } catch {videoGuard = 'waiting';}
    if (!W.find && !W.cache) {videoGuard = 'unsupported'; return;}
    if (++guardAttempts < 60) setTimeout(applyVideoGuard, 500)?.unref?.();
    else videoGuard = 'unavailable';
  }
  const base = `ws://127.0.0.1:${config.relayPort}/relay`;
  const gatewayBase = `ws://127.0.0.1:${config.relayPort}/gateway`;
  let gatewayStore = null, gatewayStarting = false, gatewayTimer = null, gatewayAttempts = 0;
  let gatewayOriginal = null, gatewayReplacement = null;
  const gateway = {constructions:0, migrations:0, lastError:null};
  function gatewayAllowed(target) {
    try {
      const u = new URL(target), q = u.searchParams;
      return u.protocol === 'wss:' && /^gateway(?:-[a-z0-9]+)*\.discord\.gg$/.test(u.hostname)
        && ['', '443'].includes(u.port) && ['', '/'].includes(u.pathname) && !u.username && !u.password && !u.hash
        && ['9','10'].includes(q.get('v')) && ['etf','json'].includes(q.get('encoding'))
        && (!q.has('compress') || ['zlib-stream','zstd-stream'].includes(q.get('compress')))
        && [...q.keys()].every(k => ['v','encoding','compress'].includes(k) && q.getAll(k).length === 1);
    } catch { return false; }
  }
  function gatewayStatus() {
    let socket; try { socket = gatewayStore?.getSocket()?.webSocket; } catch {}
    return {...gateway, throughRelay:!!socket?.url?.startsWith(gatewayBase+'?'), connected:socket?.readyState === 1};
  }
  function startGateway() {
    if (!enabled || !automatic || gatewayStarting || typeof window.DiscordProtonStreamsNative?.ensureHelper !== 'function') return;
    gatewayStore ||= findStore('GatewayConnectionStore', value => value && typeof value.getSocket === 'function');
    const retry = () => { if (enabled && automatic && ++gatewayAttempts < 60) {gatewayTimer = setTimeout(startGateway,1000);gatewayTimer?.unref?.();} };
    if (!gatewayStore) { if(W.cache || W.fluxStores)retry();return; }
    gatewayStarting = true;
    Promise.resolve().then(() => window.DiscordProtonStreamsNative.ensureHelper()).then(() => {
      if (!enabled || !automatic) return;
      if (!gatewayReplacement) {
        gatewayOriginal = window.WebSocket;
        gatewayReplacement = new Proxy(gatewayOriginal, {
          construct(Target, args) {
            const target = String(args[0]);
            if (enabled && automatic && gatewayAllowed(target)) {
              // Revalidate/relaunch a lost helper without changing the native
              // socket API. Discord retains responsibility for Resume/backoff.
              Promise.resolve().then(() => window.DiscordProtonStreamsNative.ensureHelper()).catch(() => {gateway.lastError='HelperUnavailable';});
              args = [gatewayBase+'?'+new URLSearchParams({nonce:config.nonce,target}), ...args.slice(1)];
              gateway.constructions++;
            }
            return Reflect.construct(Target,args,Target);
          }
        });
        window.WebSocket = gatewayReplacement;
      }
      const control = gatewayStore.getSocket(), socket = control?.webSocket;
      if (socket?.url?.startsWith(gatewayBase+'?')) return;
      // Fast-connect may expose no usable socket until after helper startup.
      if (!socket || !gatewayAllowed(socket.url) || socket.readyState >= 2) {retry();return;}
      // Discord can adopt a fast-connect socket created before the addon.
      // Migrate that one socket once; never alternate routes within a session.
      if (socket.readyState === 1 && control.isSessionEstablished?.()) {
        gateway.migrations++;socket.close(4000);gateway.lastError=null;
      } else retry();
    }).catch(() => {gateway.lastError='HelperUnavailable';retry();}).finally(() => {gatewayStarting=false;});
  }
  const portAllowed = new Set(['', '443', '2053', '2083', '2087', '2096', '8443']);
  function allowed(target) {
    try {
      const u = new URL(target);
      return u.protocol === 'wss:' && /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.discord\.media$/.test(u.hostname)
        && portAllowed.has(u.port) && ['', '/'].includes(u.pathname) && !u.username && !u.password && !u.hash
        && [...u.searchParams.keys()].length === 1 && ['8', '9', '10'].includes(u.searchParams.get('v'));
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
          let startup;
          try { startup = window.DiscordProtonStreamsNative.ensureHelper(); }
          catch (error) { startup = Promise.reject(error); }
          Promise.resolve(startup).then(() => {
            record.ready = true; record.starting = false;record.retries=0;record.retryAt=0;lastError=null;
            if(record.pendingFactory){const pending=record.pendingFactory;record.pendingFactory=null;if(enabled&&automatic&&wrapped.get(socket)===record&&root._socket===socket){try{record.open.apply(pending.self,pending.args);}catch(error){lastError=error?.name??'SocketFactoryError';}}}
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
      gateway:gatewayStatus(),
      streams: roots.map(x => ({state: x.state, sender: x.isStreamSender(), throughRelay: !!x._socket?.webSocket?.url?.startsWith(base)})),
      scope: 'Controle Gateway e compartilhamento de tela pelo tunel; audio principal e demais aplicativos permanecem diretos.'};
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
    if (gatewayTimer !== null) window.clearTimeout?.(gatewayTimer);
    if (gatewayReplacement && window.WebSocket === gatewayReplacement) window.WebSocket = gatewayOriginal;
    for (const [socket, record] of wrapped) if (socket.createWebSocket === record.replacement) socket.createWebSocket = record.original;
    for (const [root, record] of watchedRoots) unwatchRoot(root, record);
    wrapped.clear(); delete window[key];
    if (options.reconnectReceivers) for (const root of Object.values(store.getRTCConnections())) {
      if (root.context === 'stream' && !root.isStreamSender()) root.reconnect();
    }
    return {removed: true};
  }
  store.addChangeListener(scan);
  window[key] = {status, testarRecepcaoAtual: testReceivers, automatico(value = true) {automatic = !!value; if(automatic)startGateway();scan(); return status();}, remover: remove};
  applyVideoGuard(); scan(); return status();
})()
