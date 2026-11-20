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
  const playback = new Map();
  const qualityControls = new Map();
  const receiverReservations = new Map();
  const multiScreen = {revision:3,enabled:config.multiStreamOptimization !== false,activeReceivers:0,limitedReceivers:0,
    lowLayerReceivers:0,unsupportedReceivers:0,level:0,pixelBudget:0,adjustments:0};
  let qualityBadSamples=0,qualityGoodSamples=0;
  let playbackTimer = null, playbackDispatcher = null, videoStreamStore = null, lastPlaybackRecovery = -Infinity;
  const recovery = {attempts:0, lastReason:null,networkDeferrals:0};
  let nextReceiverOpen=0;
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
    let socket; try {
      // Read an already-loaded store without triggering Vencord's lazy lookup.
      gatewayStore ||= loaded(value => value?.constructor?.displayName === 'GatewayConnectionStore'
        && typeof value.getSocket === 'function');
      socket = gatewayStore?.getSocket()?.webSocket;
    } catch {}
    return {...gateway, routingEnabled:config.tunnelGateway === true,
      throughRelay:!!socket?.url?.startsWith(gatewayBase+'?'), connected:socket ? socket.readyState === 1 : null};
  }
  function startGateway() {
    // Voice setup, messages and presence depend on the main Gateway. Keep it
    // independent of the stream tunnel unless the user explicitly opts in.
    if (config.tunnelGateway !== true || !enabled || !automatic || gatewayStarting || typeof window.DiscordProtonStreamsNative?.ensureHelper !== 'function') return;
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
      // Independent receive connections otherwise request their first keyframe
      // together. Space new openings; never delay voice or the user's sender.
      if (enabled && automatic && !root.isStreamSender() && !record.renewed && !record.opening
        && (!record.startupScheduled || record.openTimer!=null)) {
        record.pendingOpen={self:this,args:factoryArgs};
        if (record.openTimer!=null) return;
        record.startupScheduled=true;
        const now=Date.now(), start=Math.max(now,nextReceiverOpen);nextReceiverOpen=start+750;
        if (start>now) {
          record.openTimer=setTimeout(()=>{
            record.openTimer=null;const pending=record.pendingOpen;record.pendingOpen=null;
            if (!pending || !enabled || !automatic || !record.ready || wrapped.get(socket)!==record || root._socket!==socket
              || !Object.values(store.getRTCConnections()).includes(root) || socket.connectionState===0) return;
            record.opening=true;
            try {record.open.apply(pending.self,pending.args);} catch {lastError='SocketFactoryError';}
            finally {record.opening=false;}
          },start-now);
          return;
        }
        record.pendingOpen=null;
      }
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
      try {
        attach(root);
        if (enabled && automatic && multiScreen.enabled && !root.isStreamSender()) watchQuality(root);
      } catch (error) { lastError = error?.name ?? 'RuntimeError'; }
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
      for (const root of playback.keys()) if (!roots.includes(root)) playback.delete(root);
      for (const [root, record] of watchedRoots) if (!roots.includes(root)) unwatchRoot(root, record);
      for (const root of roots) watchRoot(root);
      const activeSockets = new Set(roots.map(x => x._socket));
      for (const [socket, record] of wrapped) {
        if (!activeSockets.has(socket)) {
          if (record.openTimer!=null) window.clearTimeout?.(record.openTimer);
          record.pendingOpen=null;
          if (socket.createWebSocket === record.replacement) socket.createWebSocket = record.original;
          wrapped.delete(socket);
        }
      }
      if (!roots.some(root=>root.context==='stream' && !root.isStreamSender())) nextReceiverOpen=0;
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
            if(record.pendingFactory){const pending=record.pendingFactory;record.pendingFactory=null;if(enabled&&automatic&&wrapped.get(socket)===record&&root._socket===socket&&socket.connectionState!==0){try{record.open.apply(pending.self,pending.args);}catch(error){lastError=error?.name??'SocketFactoryError';}}}
            if (enabled && automatic) scan();
          }).catch(() => {record.starting = false;lastError='HelperUnavailable';record.retryAt=Date.now()+Math.min(5000,250*2**record.retries++);if(enabled&&automatic)setTimeout(scan,Math.max(0,record.retryAt-Date.now()));});
        }
        if (automatic && record && record.ready && !record.renewed && root.state === 'RTC_CONNECTED' && !socket.webSocket?.url?.startsWith(base)) {
          record.renewed = true;
          setTimeout(() => {
            if (enabled && automatic && Object.values(store.getRTCConnections()).includes(root) && root._socket === socket) root.reconnect();
          }, 0);
        }
      }
      optimizeScreens(roots);
    } catch (error) { lastError = error?.name ?? 'RuntimeError'; }
  }
  function qualityHook(record,name,replacement) {
    const descriptor=Object.getOwnPropertyDescriptor(record.manager,name), original=record.manager[name];
    if (typeof original!=='function' || descriptor && (!descriptor.configurable || !descriptor.writable)) return false;
    record.methods.push({name,descriptor,original,replacement});record.manager[name]=replacement;return true;
  }
  function releaseQuality(root,record,refresh=false) {
    qualityControls.delete(root);
    const q=record.manager;
    for (const method of record.methods) if (q[method.name]===method.replacement) {
      if (method.descriptor) Object.defineProperty(q,method.name,method.descriptor);
      else delete q[method.name];
    }
    if (q.videoStream===record.selected && record.nativeStream) q.videoStream=record.nativeStream;
    if (refresh && root._goLiveQualityManager===q && root.state==='RTC_CONNECTED') {
      try {q.update();} catch {lastError='StreamQualityRestoreFailed';}
    }
  }
  function watchQuality(root) {
    const q=root._goLiveQualityManager;
    let record=qualityControls.get(root);
    if (record?.manager===q) return record;
    const previous=record;
    if (record) releaseQuality(root,record);
    if (!q || typeof q.emit!=='function' || typeof q.update!=='function') return null;
    record={manager:q,methods:[],cap:previous?.cap??Infinity,lower:previous?.lower??false,layers:[],layersSeen:false,nativeStream:q.videoStream,
      selected:null,sample:null,previous:null};
    const originalEmit=q.emit;
    if (!qualityHook(record,'emit',function(event,...args) {
      // Keep Discord's natural dimensions/zoom intact, including zero pixels
      // for hidden tiles. Only bound the request emitted to the SFU/sender.
      const wants=args[0];
      if (event==='requested-streams-update' && Number.isFinite(record.cap) && wants?.pixelCounts) {
        const pixels={...wants.pixelCounts};
        for (const ssrc of Object.keys(pixels)) if (numeric(pixels[ssrc]) && pixels[ssrc]>0) {
          pixels[ssrc]=Math.min(pixels[ssrc],record.cap);
        }
        args[0]={...wants,pixelCounts:pixels};
      }
      return originalEmit.call(this,event,...args);
    })) return null;
    const originalInfo=q.updateAudioAndVideoStreamInfo, originalRequest=q.requestStream;
    if (typeof originalInfo==='function' && typeof originalRequest==='function') {
      qualityHook(record,'updateAudioAndVideoStreamInfo',function(audio,layers,...args) {
        record.layersSeen=Array.isArray(layers);
        record.layers=Array.isArray(layers)?layers.filter(layer=>layer?.active && numeric(layer.quality)):[];
        record.nativeStream=record.layers[0];
        return originalInfo.call(this,audio,layers,...args);
      });
      qualityHook(record,'requestStream',function(...args) {
        // Select only layers actually advertised by this sender. Never invent
        // an SSRC or pause a screen to create the appearance of lower load.
        const native=record.nativeStream;
        const lower=record.lower && native && record.layers.filter(layer=>layer.quality<native.quality)
          .sort((a,b)=>a.quality-b.quality)[0];
        if (lower || record.selected && this.videoStream===record.selected) this.videoStream=lower || native;
        record.selected=lower || null;
        return originalRequest.apply(this,args);
      });
    }
    qualityControls.set(root,record);return record;
  }
  function optimizeScreens(roots,measure=false) {
    const currentRoots=Object.values(store.getRTCConnections());
    roots=roots.filter(root=>currentRoots.includes(root));
    for (const [root,record] of qualityControls) if (!roots.includes(root) || !multiScreen.enabled || !automatic) {
      releaseQuality(root,record,!multiScreen.enabled || !automatic);
    }
    const receivers=roots.filter(root=>root.context==='stream' && !root.isStreamSender());
    for (const root of receiverReservations.keys()) if (!receivers.includes(root) || !enabled || !automatic || !multiScreen.enabled) {
      receiverReservations.delete(root);
    }
    if (enabled && automatic && multiScreen.enabled) for (const root of receivers) watchQuality(root);
    // Native reconnect can clear the socket, replace the manager and zero its
    // dimensions before the next routed connection. Preserve the last visible
    // demand through that gap, bounded to 30s; removed/paused screens release it.
    const now=Date.now(), demands=[];
    for (const root of receivers) {
      const q=root._goLiveQualityManager, previous=receiverReservations.get(root);
      const zoom=numeric(q?.zoom) && q.zoom>0?q.zoom:1;
      const value=numeric(q?.resolutionWidth) && numeric(q?.resolutionHeight)
        ?q.resolutionWidth*q.resolutionHeight*zoom*zoom:0;
      const pixels=numeric(value) && value>0?value:0;
      const requested=currentReceiver(root), connected=root.state==='RTC_CONNECTED';
      if (requested && connected) {
        receiverReservations.set(root,{manager:q,pixels,until:now+30000});
        demands.push({root,pixels});continue;
      }
      const explicitPause=q?.incomingVideoEnabled===false || q?.videoStream?.active===false
        || (q===previous?.manager && numeric(q?.resolutionWidth) && numeric(q?.resolutionHeight) && !pixels);
      if (!connected && previous && previous.until>now && !explicitPause) {
        demands.push({root,pixels:pixels || previous.pixels});
      } else if (requested && root.state!=='RTC_DISCONNECTED') demands.push({root,pixels});
      else receiverReservations.delete(root);
    }
    const active=demands.map(row=>row.root);
    multiScreen.activeReceivers=active.length;
    if (!enabled || !automatic || !multiScreen.enabled || active.length<2) {
      multiScreen.level=0;qualityBadSamples=0;qualityGoodSamples=0;
    } else if (measure) {
      let packets=0,lost=0,healthy=true;
      for (const root of active) {
        if (root.state!=='RTC_CONNECTED') {healthy=false;continue;}
        const record=qualityControls.get(root), sample=playback.get(root)?.sample;
        if (!record || !sample || !sample.requested || record.sample===sample) {healthy=false;continue;}
        record.sample=sample;
        const previous=record.previous;record.previous={connection:root._connection,...sample};
        if (!previous || previous.connection!==root._connection || !numeric(sample.packets) || !numeric(sample.packetsLost)
          || sample.packets<previous.packets || sample.packetsLost<previous.packetsLost) {healthy=false;continue;}
        if (sample.frames<=previous.frames || sample.packets<=previous.packets) healthy=false;
        packets+=sample.packets-previous.packets;lost+=sample.packetsLost-previous.packetsLost;
      }
      // Cumulative counters must be differenced. Missing stats, reset counters,
      // static video and tiny samples cannot establish congestion or recovery.
      if (packets+lost>=50) {
        const fraction=lost/(packets+lost);
        qualityBadSamples=fraction>=.08?qualityBadSamples+1:0;
        qualityGoodSamples=healthy && fraction<=.02?qualityGoodSamples+1:0;
        if (qualityBadSamples>=2 && multiScreen.level<3) {multiScreen.level++;qualityBadSamples=0;qualityGoodSamples=0;}
        if (qualityGoodSamples>=6 && multiScreen.level>0) {multiScreen.level--;qualityGoodSamples=0;}
      } else {qualityBadSamples=0;qualityGoodSamples=0;}
    }
    const total=demands.reduce((sum,row)=>sum+row.pixels,0);
    const limited=enabled && automatic && multiScreen.enabled && active.length>=2;
    // One shared 720p pixel budget, weighted by displayed area. Grid tiles get
    // less than a focused/zoomed tile; loss reduces the aggregate request.
    const budget=limited?Math.min(1280*720,total/Math.sqrt(active.length))*[1,.65,.4,.25][multiScreen.level]:0;
    multiScreen.pixelBudget=Math.floor(budget);multiScreen.limitedReceivers=0;
    multiScreen.lowLayerReceivers=0;multiScreen.unsupportedReceivers=0;
    multiScreen.measuredBitrateBps=0;multiScreen.measuredReceivers=0;
    multiScreen.lowerLayerAvailableReceivers=0;multiScreen.layerInfoPendingReceivers=0;
    for (const root of active) if (!qualityControls.has(root)) multiScreen.unsupportedReceivers++;
    for (const root of active) {
      const playbackRecord=playback.get(root), sample=playbackRecord?.sample, record=qualityControls.get(root);
      if (sample && numeric(sample.bitrateBps) && numeric(playbackRecord.sampleAt)
        && Date.now()-playbackRecord.sampleAt>=0 && Date.now()-playbackRecord.sampleAt<=15000) {
        multiScreen.measuredBitrateBps+=sample.bitrateBps;multiScreen.measuredReceivers++;
      }
      if (record && !record.layersSeen) multiScreen.layerInfoPendingReceivers++;
      if (record?.layers.some(layer=>layer.quality<(record.nativeStream?.quality??0))) multiScreen.lowerLayerAvailableReceivers++;
    }
    const plans=[...qualityControls].map(([root,record])=>{
      const demand=demands.find(row=>row.root===root && row.pixels>0);
      const cap=limited && demand?Math.max(1,Math.floor(Math.min(demand.pixels,budget*demand.pixels/total))):Infinity;
      // Use separate focus thresholds so near-equal tiles do not alternate
      // layers as fractional layout sizes change by a pixel.
      const lower=limited && !!demand && (multiScreen.level>=2 || demand.pixels/total<=(record.lower ? .6 : .55));
      return {root,record,demand,cap,lower};
    });
    const rebalance=plans.some(({record,cap,lower})=>lower!==record.lower || cap!==record.cap
      && (!Number.isFinite(cap) || !Number.isFinite(record.cap) || Math.abs(cap-record.cap)/Math.max(1,record.cap)>=.1));
    for (const {root,record,demand,cap,lower} of plans) {
      if (Number.isFinite(cap)) multiScreen.limitedReceivers++;
      // Rebalance all shares together: accepting only the larger increases
      // while suppressing small decreases would exceed the aggregate budget.
      if (rebalance && (cap!==record.cap || lower!==record.lower)) {
        record.cap=cap;record.lower=lower;multiScreen.adjustments++;
        if (root.state==='RTC_CONNECTED') try {record.manager.update();} catch {lastError='StreamQualityUpdateFailed';}
      }
      if (record.selected) multiScreen.lowLayerReceivers++;
      if (!limited || !demand) {record.previous=null;record.sample=null;}
    }
  }
  function playbackRecord(root) {
    let record = playback.get(root);
    if (!record) {
      record = {connection:null, previous:null, stalledSince:null, attempts:[], nextRetry:0, pending:null,
        networkCongestedUntil:0,networkDeferralSince:null,
        timeoutPending:false, timeoutConnection:null, timeoutVersion:0, nativeTimeoutSeen:false, sample:null,sampleAt:null};
      playback.set(root,record);
    }
    return record;
  }
  function currentReceiver(root) {
    return enabled && automatic && root.context === 'stream' && !root.isStreamSender()
      && Object.values(store.getRTCConnections()).includes(root)
      && videoRequested(root)
      && !!root._socket?.webSocket?.url?.startsWith(base+'?');
  }
  function videoRequested(root) {
    const quality=root._goLiveQualityManager;
    if (quality?.incomingVideoEnabled === false || quality?.videoStream?.active === false) return false;
    // Discord can keep sinkWant=100 while requesting pixelCounts=0 for a
    // hidden tile. That is an intentional pause, not a first-frame failure.
    if (numeric(quality?.resolutionWidth) && numeric(quality?.resolutionHeight)) {
      return quality.resolutionWidth>0 && quality.resolutionHeight>0;
    }
    return true; // Older clients expose only the per-SSRC sinkWant counters.
  }
  function recoverPlayback(root, reason) {
    if (!currentReceiver(root) || typeof root.reconnect !== 'function') return;
    const record = playbackRecord(root), now = Date.now();
    if (reason==='video-decode-stalled' && record.networkCongestedUntil>now) {
      // Missing video under active packet loss is not proof that the decoder
      // failed. Give the quality controller time before requesting another
      // large initial keyframe. The grace is bounded, not an infinite veto.
      if (record.networkDeferralSince===null) {record.networkDeferralSince=now;recovery.networkDeferrals++;}
      if (now-record.networkDeferralSince<30000) return;
    }
    record.attempts = record.attempts.filter(at => now-at < 300000);
    // A server/encoder outage must not create a reconnect storm across screens.
    if (now < record.nextRetry || now-lastPlaybackRecovery < 5000 || record.attempts.length >= 2) return;
    record.attempts.push(now);record.nextRetry=now+30000;lastPlaybackRecovery=now;
    record.stalledSince=null;record.previous=null;
    record.networkDeferralSince=null;
    recovery.attempts++;recovery.lastReason=reason;
    try {root.reconnect();} catch {lastError='PlaybackReconnectFailed';}
    // Keep Discord's timeout/error visible until its native first-frame event
    // resolves it. Never clear an error merely because reconnect was requested.
  }
  function onVideoTimeout(event) {
    if (event?.mediaContext !== 'stream') return;
    for (const [streamKey,root] of Object.entries(store.getRTCConnections())) {
      if (event.streamKey != null ? event.streamKey === streamKey
        : event.userId != null && event.userId === root._goLiveQualityManager?.userId) {
        const record=playbackRecord(root), version=++record.timeoutVersion;
        const connection=root._connection, socket=root._socket;
        // Flux can still be dispatching; reconnect on the next event-loop turn.
        setTimeout(() => {
          if (playback.get(root)!==record || record.timeoutVersion!==version
            || root._connection!==connection || root._socket!==socket || !currentReceiver(root)) return;
          record.timeoutPending=true;record.timeoutConnection=connection;record.nativeTimeoutSeen=true;
          recoverPlayback(root,'first-frame-timeout');
        },0);
      }
    }
  }
  function clearPlaybackTimeout(record) {
    record.timeoutPending=false;record.timeoutConnection=null;record.timeoutVersion++;
  }
  function onVideoReady(event) {
    if (event?.mediaContext !== 'stream') return;
    for (const [streamKey,root] of Object.entries(store.getRTCConnections())) {
      if (event.streamKey != null ? event.streamKey === streamKey
        : event.userId != null && event.userId === root._goLiveQualityManager?.userId) {
        const record=playback.get(root);
        if (record) clearPlaybackTimeout(record);
      }
    }
  }
  function detectVideoTimeouts(roots) {
    // The 2012 timeout may predate our Flux subscription, or happen while a
    // tile is hidden. Read native state as well as listening for new events.
    try {
      videoStreamStore ||= findStore('VideoStreamStore', value => value && typeof value.getTimedoutVideos === 'function');
      const timeouts=videoStreamStore?.getTimedoutVideos();
      if (!timeouts || typeof timeouts!=='object' || Array.isArray(timeouts)) return;
      const events=Object.values(timeouts);
      const connections=store.getRTCConnections();
      for (const [streamKey,root] of Object.entries(connections)) {
        if (!roots.includes(root) || root.isStreamSender()) continue;
        const record=playbackRecord(root);
        const timeout=events.find(event => event?.mediaContext==='stream'
          && (event.streamKey!=null ? event.streamKey===streamKey
            : event.userId!=null && event.userId===root._goLiveQualityManager?.userId));
        if (!timeout) {record.nativeTimeoutSeen=false;continue;}
        if (record.nativeTimeoutSeen || root.state!=='RTC_CONNECTED' || !currentReceiver(root)) continue;
        record.nativeTimeoutSeen=true;
        // Keep a stale native error latched after decoded progress confirms
        // success; only a new event or disappearance/reappearance rearms it.
        if (!record.timeoutPending) {
          record.timeoutPending=true;record.timeoutConnection=root._connection;
        }
      }
    } catch {} // Native store availability varies across Discord versions.
  }
  function startPlaybackMonitor() {
    if (!enabled || !automatic) return;
    if (!playbackDispatcher) {
      try {
        const usable = value => value && typeof value.subscribe === 'function' && typeof value.unsubscribe === 'function';
        const dispatcher = usable(W.Common?.FluxDispatcher) ? W.Common.FluxDispatcher : loaded(usable);
        if (dispatcher) {
          dispatcher.subscribe('VIDEO_STREAM_READY_TIMEOUT',onVideoTimeout);
          dispatcher.subscribe('CLEAR_VIDEO_STREAM_READY_TIMEOUT',onVideoReady);
          playbackDispatcher=dispatcher;
        }
      } catch {}
    }
    if (playbackTimer === null && typeof setInterval === 'function') {
      playbackTimer=setInterval(samplePlayback,5000);playbackTimer?.unref?.();
    }
  }
  const numeric = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  async function samplePlayback() {
    if (!enabled || !automatic) return;
    startPlaybackMonitor();
    const roots=Object.values(store.getRTCConnections()).filter(root => root.context === 'stream');
    for (const root of playback.keys()) if (!roots.includes(root)) playback.delete(root);
    detectVideoTimeouts(roots);
    await Promise.all(roots.map(async root => {
      const record=playbackRecord(root), connection=root._connection;
      if (record.connection !== connection || root.state !== 'RTC_CONNECTED') {
        record.connection=connection;record.previous=null;record.stalledSince=null;record.sample=null;record.pending=null;
        record.sampleAt=null;
        record.networkCongestedUntil=0;record.networkDeferralSince=null;
      }
      // A native timeout is evidence even if stats are missing or never settle.
      // Do not let a request from a destroyed connection block its successor.
      if (record.pending || !connection || root.state !== 'RTC_CONNECTED' || typeof connection.getStats !== 'function') {
        if (record.timeoutPending && root.state === 'RTC_CONNECTED') recoverPlayback(root,'first-frame-timeout');
        return;
      }
      const pending={};record.pending=pending;
      try {
        const stats=await connection.getStats();
        if (!enabled || !automatic || root.state !== 'RTC_CONNECTED' || root._connection !== connection || !roots.includes(root)
          || playback.get(root)!==record || record.pending!==pending || !Object.values(store.getRTCConnections()).includes(root)) return;
        const sender=root.isStreamSender();
        const entries=sender ? stats?.rtp?.outbound : Object.values(stats?.rtp?.inbound || {}).flat();
        if (!Array.isArray(entries)) return;
        const videos=entries.filter(row => row?.type === 'video');
        const selected=sender ? videos : videoRequested(root)
          ? videos.filter(row => numeric(row.sinkWantLocalAsInt) && row.sinkWantLocalAsInt > 0) : [];
        const sum=(name,rows=selected) => rows.reduce((total,row) => total+(numeric(row[name])?row[name]:0),0);
        const sample={sender,requested:selected.length>0,frames:sum(sender?'framesEncoded':'framesDecoded'),
          packets:sum(sender?'packetsSent':'packetsReceived'),packetsLost:sum('packetsLost'),
          decodeErrors:sum('framesDecodeErrors'),decryptFailures:sum(sender?'encryptFailureCount':'decryptFailureCount')};
        const byteKey=sender?'bytesSent':'bytesReceived';
        if (selected.length && selected.every(row=>numeric(row[byteKey]))) sample.bytes=sum(byteKey);
        // Hidden/unrequested video must not trigger recovery, but clearing
        // its counters used to erase loss/decoder evidence from diagnostics
        // as soon as the user switched away to collect a report.
        record.sample={...sample,frames:sum(sender?'framesEncoded':'framesDecoded',videos),
          packets:sum(sender?'packetsSent':'packetsReceived',videos),packetsLost:sum('packetsLost',videos),
          decodeErrors:sum('framesDecodeErrors',videos),decryptFailures:sum(sender?'encryptFailureCount':'decryptFailureCount',videos)};
        record.sampleAt=Date.now();
        const previous=record.previous, now=Date.now();record.previous={...sample,at:now};
        const elapsed=previous?now-previous.at:0;
        if (previous?.requested && sample.requested && elapsed>0 && elapsed<=15000) {
          if (numeric(sample.bytes) && numeric(previous.bytes) && sample.bytes>=previous.bytes)
            record.sample.bitrateBps=Math.round((sample.bytes-previous.bytes)*8000/elapsed);
          if (!sender && sample.frames>=previous.frames)
            record.sample.decodeFps=Math.round((sample.frames-previous.frames)*10000/elapsed)/10;
          const packets=sample.packets-previous.packets, lost=sample.packetsLost-previous.packetsLost;
          if (!sender && packets>=0 && lost>=0 && packets+lost>=50) {
            record.sample.lossPercent=Math.round(lost/(packets+lost)*1000)/10;
            if (lost/(packets+lost)>=.08) record.networkCongestedUntil=now+15000;
            else if (sample.frames>previous.frames) {record.networkCongestedUntil=0;record.networkDeferralSince=null;}
          }
        }
        // Counters reset on a new native media connection. A positive decoded
        // count there also confirms recovery; historical frames here do not.
        if (!sender && sample.requested && selected.every(row => numeric(row.framesDecoded)) && sample.frames>0
          && (record.timeoutConnection!==connection || previous?.requested && sample.frames>previous.frames)) clearPlaybackTimeout(record);
        const comparable=!sender && selected.length>0 && selected.every(row => numeric(row.framesDecoded) && numeric(row.packetsReceived));
        // Static/paused video and unrequested simulcast layers are healthy.
        // Only continuous incoming VIDEO without decoded progress is evidence.
        if (!comparable || !previous?.requested || sample.frames !== previous.frames || sample.packets <= previous.packets) {
          record.networkDeferralSince=null;
          record.stalledSince=null;return;
        }
        record.stalledSince ??= now;
        if (now-record.stalledSince >= 20000) recoverPlayback(root,'video-decode-stalled');
      } catch {if(record.pending===pending){record.previous=null;record.stalledSince=null;}}
      finally {
        if (record.pending===pending) {
          record.pending=null;
          if (playback.get(root)===record && record.timeoutPending && root._connection===connection
            && root.state==='RTC_CONNECTED') recoverPlayback(root,'first-frame-timeout');
        }
      }
    }));
    try {optimizeScreens(roots,true);} catch {lastError='StreamQualitySampleFailed';}
    try {window.DiscordProtonStreamsNative?.reportPlayback?.(playbackStatus());} catch {}
  }
  function playbackStatus() {
    return {...recovery,multiScreen:{...multiScreen,lowLayerReceivers:[...qualityControls.values()].filter(record=>record.selected).length},streams:[...playback.entries()].map(([root,record]) => {
      const sample={...(record.sample || {sender:root.isStreamSender(),requested:false})};
      if (!numeric(record.sampleAt) || Date.now()-record.sampleAt<0 || Date.now()-record.sampleAt>15000) {
        for (const name of ['bitrateBps','decodeFps','lossPercent']) delete sample[name];
      }
      return {state:['RTC_CONNECTED','RTC_CONNECTING','RTC_DISCONNECTED','NO_ROUTE','AWAITING_ENDPOINT'].includes(root.state)?root.state:'OTHER',
        ...sample,timeoutPending:record.timeoutPending,recoveryAttempts:record.attempts.length};
    })};
  }
  function status() {
    const roots = Object.values(store.getRTCConnections()).filter(x => x.context === 'stream');
    return {enabled, automatic, constructions, wrappedSockets: wrapped.size, lastError, videoGuard,
      gateway:gatewayStatus(),
      playback:playbackStatus(),
      streams: roots.map(x => ({state: x.state, sender: x.isStreamSender(), throughRelay: !!x._socket?.webSocket?.url?.startsWith(base)})),
      scope: config.tunnelGateway === true
        ? 'Controle Gateway e compartilhamento de tela pelo tunel; audio principal e demais aplicativos permanecem diretos.'
        : 'Somente controle e midia das telas pelo tunel; Gateway principal, voz e demais aplicativos permanecem diretos.'};
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
    if (playbackTimer !== null) window.clearInterval?.(playbackTimer);
    try {playbackDispatcher?.unsubscribe('VIDEO_STREAM_READY_TIMEOUT',onVideoTimeout);} catch {}
    try {playbackDispatcher?.unsubscribe('CLEAR_VIDEO_STREAM_READY_TIMEOUT',onVideoReady);} catch {}
    playback.clear();
    receiverReservations.clear();
    for (const [root,record] of qualityControls) releaseQuality(root,record,true);
    if (gatewayReplacement && window.WebSocket === gatewayReplacement) window.WebSocket = gatewayOriginal;
    for (const [socket, record] of wrapped) if (socket.createWebSocket === record.replacement) socket.createWebSocket = record.original;
    for (const record of wrapped.values()) {
      if (record.openTimer!=null) window.clearTimeout?.(record.openTimer);
      record.pendingOpen=null;
    }
    for (const [root, record] of watchedRoots) unwatchRoot(root, record);
    wrapped.clear(); delete window[key];
    if (options.reconnectReceivers) for (const root of Object.values(store.getRTCConnections())) {
      if (root.context === 'stream' && !root.isStreamSender()) root.reconnect();
    }
    return {removed: true};
  }
  store.addChangeListener(scan);
  window[key] = {status, testarRecepcaoAtual: testReceivers,
    otimizarTelas(value=true) {multiScreen.enabled=!!value;scan();return status();},
    automatico(value = true) {automatic = !!value; if(automatic){startGateway();startPlaybackMonitor();}scan(); return status();}, remover: remove};
  applyVideoGuard(); scan(); return status();
})()
