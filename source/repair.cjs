'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const START='\n/* DISCORD_STREAM_TUNNEL_V2_BEGIN */\n',END='\n/* DISCORD_STREAM_TUNNEL_V2_END */\n';
const pairs=[[START,END],['\n/* DISCORD_PROTON_STREAMS_LOCAL_BEGIN */\n','\n/* DISCORD_PROTON_STREAMS_LOCAL_END */\n'],['\n/* DISCORD_STREAM_TUNNEL_BEGIN_V1 */\n','\n/* DISCORD_STREAM_TUNNEL_END_V1 */\n']];
function clean(text){for(const[a,b]of pairs){if(!text.includes(a))continue;if(text.split(a).length!==2||text.split(b).length!==2)throw Error('AmbiguousLoader');const i=text.indexOf(a),j=text.indexOf(b,i+a.length);if(j<0)throw Error('MalformedLoader');text=text.slice(0,i)+text.slice(j+b.length);}return text;}
function ensure(root){const install=JSON.parse(fs.readFileSync(path.join(root,'installation.json'),'utf8')),target=install.preload;
 const original=fs.readFileSync(target,'utf8');if(!original.slice(0,1500).includes('Vencord'))throw Error('UnexpectedPreload');
 const bare=clean(original),patched=bare+START+'try{require('+JSON.stringify(path.join(root,'bootstrap.cjs'))+');}catch{}'+END;
 if(patched===original)return false;
 const backup=path.join(root,'data','preload-'+crypto.createHash('sha256').update(bare).digest('hex').slice(0,16)+'.js');
 if(!fs.existsSync(backup))fs.writeFileSync(backup,bare,{mode:0o600});
 const temp=target+'.dts-new',mode=fs.statSync(target).mode;fs.writeFileSync(temp,patched,{mode});fs.renameSync(temp,target);return true;
}
module.exports={ensure,clean,START,END};
