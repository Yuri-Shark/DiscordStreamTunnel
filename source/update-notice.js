(() => {
 'use strict';
 const key='__discordStreamTunnelUpdateNotice';
 if(window[key])return;
 let node=null,shown=null,dismissed=null,busy=false,pending=false,refreshAgain=false,timer=null,enabled=true;
 const native=()=>window.DiscordStreamTunnelUpdatesNative;
 function element(tag,text,style){const item=document.createElement(tag);if(text)item.textContent=text;if(style)Object.assign(item.style,style);return item;}
 function close(){node?.remove();node=null;}
 function show(version){
  close();shown=version;
  const panel=element('section',null,{position:'fixed',top:'16px',right:'16px',width:'360px',maxWidth:'calc(100vw - 32px)',boxSizing:'border-box',padding:'16px',borderRadius:'10px',background:'var(--background-floating, #232428)',color:'var(--text-normal, #f2f3f5)',border:'1px solid var(--background-modifier-accent, #4e5058)',boxShadow:'0 8px 24px rgba(0,0,0,.35)',zIndex:'2147483646',fontFamily:'var(--font-primary, sans-serif)'});
  panel.id='dts-update-notice';panel.setAttribute('role','status');panel.setAttribute('aria-label','Atualização do DiscordStreamTunnel pronta');
  const title=element('div','Atualização pronta',{fontSize:'16px',fontWeight:'700',marginBottom:'4px'});
  const detail=element('div','DiscordStreamTunnel '+version,{fontSize:'14px',marginBottom:'10px',color:'var(--text-muted, #b5bac1)'});
  const message=element('p','A atualização foi baixada e verificada. Reiniciar encerra as chamadas e transmissões atuais.',{fontSize:'14px',lineHeight:'1.4',margin:'0 0 14px'});
  const actions=element('div',null,{display:'flex',justifyContent:'flex-end',gap:'8px'});
  const later=element('button','Depois',{background:'transparent',color:'inherit',border:'none',borderRadius:'4px',padding:'9px 12px',fontSize:'14px',cursor:'pointer'});
  const restart=element('button','Reiniciar e aplicar',{background:'var(--brand-500, #5865f2)',color:'#fff',border:'none',borderRadius:'4px',padding:'9px 12px',fontSize:'14px',fontWeight:'600',cursor:'pointer'});
  later.type=restart.type='button';restart.setAttribute('aria-label','Reiniciar Discord e aplicar atualização '+version);
  later.addEventListener('click',()=>{dismissed=version;close();});
  restart.addEventListener('click',async()=>{
   if(busy)return;
   busy=true;restart.disabled=true;later.disabled=true;restart.textContent='Reiniciando…';
   try{
    const relaunch=window.DiscordNative?.app?.relaunch;
    if(typeof relaunch!=='function')throw Error('RelaunchUnavailable');
    const result=await native().prepareRestart(version);
    if(result?.ready!==true||result.version!==version)throw Error('UpdateNotReady');
    await window.DiscordNative.app.relaunch();
   }catch{
    message.textContent='Não foi possível reiniciar. Tente novamente ou feche e abra o Discord para aplicar a atualização.';
    restart.textContent='Tentar novamente';restart.disabled=false;later.disabled=false;busy=false;
   }
  });
  actions.append(later,restart);panel.append(title,detail,message,actions);document.body.append(panel);node=panel;
 }
 async function refresh(){
  if(!enabled||busy)return;
  if(pending){refreshAgain=true;return;}
  pending=true;
  try{
   const update=await native()?.getState();
   if(!enabled)return;
   if(update?.ready&&/^\d+\.\d+\.\d+$/.test(update.availableVersion)&&document.body){
    if(update.availableVersion!==dismissed&&update.availableVersion!==shown)show(update.availableVersion);
   }
   if(update?.phase==='checking'||!document.body){timer=setTimeout(refresh,1000);}
  }catch{}finally{pending=false;if(enabled&&refreshAgain){refreshAgain=false;Promise.resolve().then(refresh);}}
 }
 window[key]={refresh,remove(){enabled=false;clearTimeout(timer);close();delete window[key];}};
 refresh();
})()
