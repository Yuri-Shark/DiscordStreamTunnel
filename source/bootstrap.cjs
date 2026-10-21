'use strict';
const fs=require('fs'),path=require('path'),root=__dirname;
try{const active=JSON.parse(fs.readFileSync(path.join(root,'active.json'),'utf8'));
 if(!/^\d+\.\d+\.\d+$/.test(active.version))throw Error('InvalidVersion');
 require(path.join(root,'versions',active.version,'source','native.cjs')).start(root);
}catch{}
