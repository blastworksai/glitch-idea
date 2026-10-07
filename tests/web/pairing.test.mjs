// Fragment pairing: the page redeems /#pair=<code> itself, after removing it from the address.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web=new URL('../../glitch-idea/web/',import.meta.url);
const url=text=>'data:text/javascript;base64,'+Buffer.from(text).toString('base64');
const apiUrl=url(await readFile(new URL('api.js',web),'utf8'));
const foldsUrl=url(await readFile(new URL('folds.js',web),'utf8'));
const app=(await readFile(new URL('app.js',web),'utf8')).replace("'./api.js'",JSON.stringify(apiUrl)).replace("'./folds.js'",JSON.stringify(foldsUrl));
const {startApp,pairFromFragment}=await import(url(app));
const CODE='0123456789abcdef'.repeat(2);
const EXPIRED="This link's pairing code has expired. Ask your terminal for a new code (it runs session-open --resume).";

function recorder(pairImpl) {
  const calls=[];
  const history={replaceState:(...args)=>calls.push(['replaceState',...args])};
  const api={pair:async code=>{calls.push(['pair',code]);return pairImpl(code);},session:async()=>{calls.push(['session']);return {agent_status:'disconnected'};}};
  return {calls,history,api};
}
const loc=hash=>({hash,pathname:'/',search:'?idea_id=x'});

test('replaceState runs before pair, and pair gets the fragment code',async()=>{
  const r=recorder(async()=>({session_id:'s'}));
  const out=await pairFromFragment({location:loc('#pair='+CODE),history:r.history,api:r.api});
  assert.deepEqual(r.calls.map(c=>c[0]),['replaceState','pair']);
  assert.deepEqual(r.calls[0].slice(1),[null,'','/?idea_id=x']);
  assert.equal(r.calls[1][1],CODE);
  assert.equal(out.attempted,true);assert.deepEqual(out.session,{session_id:'s'});
});

test('non-matching hashes make no pair call',async()=>{
  for(const hash of ['','#top','#pair=','#pair='+CODE.toUpperCase(),'#pair='+CODE.slice(1),'#pair='+CODE+'0','#x#pair='+CODE]){
    const r=recorder(async()=>({}));
    const out=await pairFromFragment({location:loc(hash),history:r.history,api:r.api});
    assert.equal(r.calls.some(c=>c[0]==='pair'),false,hash);
    assert.equal(out.attempted,false);
  }
});

test('a malformed #pair= fragment is stripped from the address, an unrelated hash is left alone',async()=>{
  let r=recorder(async()=>({}));
  await pairFromFragment({location:loc('#pair=nope'),history:r.history,api:r.api});
  assert.deepEqual(r.calls.map(c=>c[0]),['replaceState']);
  r=recorder(async()=>({}));
  await pairFromFragment({location:loc('#top'),history:r.history,api:r.api});
  assert.deepEqual(r.calls,[]);
});

test('the code is never written to any storage stub',async()=>{
  const writes=[];
  const store={setItem:(...a)=>writes.push(a),getItem:()=>null};
  const saved=[Object.getOwnPropertyDescriptor(globalThis,'localStorage'),Object.getOwnPropertyDescriptor(globalThis,'sessionStorage')];
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:store});
  Object.defineProperty(globalThis,'sessionStorage',{configurable:true,value:store});
  try {
    const r=recorder(async()=>({}));
    await pairFromFragment({location:loc('#pair='+CODE),history:r.history,api:r.api});
    assert.equal(JSON.stringify(writes).includes(CODE),false);
    assert.equal(writes.length,0);
  } finally {
    ['localStorage','sessionStorage'].forEach((n,i)=>saved[i]?Object.defineProperty(globalThis,n,saved[i]):delete globalThis[n]);
  }
});

test('a failed redeem shows the expired-link sentence and the typed input',async()=>{
  class Node {
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.disabled=false;this.value='';this.classList={add(){},remove(){},toggle(){}};this.style={};}
    append(...n){this.children.push(...n);}
    replaceChildren(...n){this.children=n;}
    setAttribute(k,v){this[k]=v;}
    addEventListener(k,f){this.listeners[k]=f;}
    removeAttribute(){}
    focus(){}
    contains(n){return n===this||this.children.some(c=>c.contains?.(n));}
  }
  const ids=['announcement','identity','progress','save-status','agent-status','compact-nav','columns','connection'];
  const roots=ids.map(id=>{const n=new Node();n.id=id;return n;});
  const find=(n,id)=>n.id===id?n:n.children.map(c=>c.children&&find(c,id)).find(Boolean);
  const text=n=>(n.textContent??'')+n.children.map(c=>typeof c==='string'?c:text(c)).join('|');
  const doc={body:new Node('body'),activeElement:null,hidden:true,createElement:t=>new Node(t),getElementById:id=>roots.map(r=>find(r,id)).find(Boolean)??null};
  doc.activeElement=doc.body;
  const names=['document','location','history','addEventListener'];
  const saved=new Map(names.map(n=>[n,Object.getOwnPropertyDescriptor(globalThis,n)]));
  const calls=[];
  globalThis.document=doc;
  Object.defineProperty(globalThis,'location',{configurable:true,value:new URL('http://127.0.0.1:1234/#pair='+CODE)});
  globalThis.history={replaceState:()=>calls.push('replaceState')};
  globalThis.addEventListener=()=>{};
  const api={pair:async()=>{calls.push('pair');throw Object.assign(new Error('x'),{status:401,code:'pairing_expired'});},session:async()=>{calls.push('session');return {};}};
  let flow;
  try {
    flow=startApp(api);
    await new Promise(r=>setImmediate(r));
    const box=doc.getElementById('connection');
    assert.equal(box.hidden,false);
    const all=text(box);
    assert.ok(all.includes(EXPIRED),all);
    assert.ok(doc.getElementById('pairing-code'),'typed input present');
    assert.equal(doc.getElementById('pairing-code').type,'password');
    assert.deepEqual(calls,['replaceState','pair']);
    assert.equal(all.includes(CODE),false);
  } finally {
    flow?.dispose();
    for(const[n,d]of saved){if(d)Object.defineProperty(globalThis,n,d);else delete globalThis[n];}
  }
});
