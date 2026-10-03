const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const ts = require('../node_modules/typescript');
function load(file, context = {}) {
  const exports = {};
  const source = fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,
    { exports, ...context });
  return exports;
}
const { applyScanEvent } = load('lib/scanEvents.ts');
test('one shared scan is upserted without a duplicate and deleted on every surface', () => {
  let scans = [{id:'a',box_quantity:1}];
  scans = applyScanEvent(scans, {type:'scan_upsert',scan:{id:'a',box_quantity:2}});
  assert.equal(scans.length, 1); assert.equal(scans[0].box_quantity, 2);
  scans = applyScanEvent(scans, {type:'scan_upsert',scan:{id:'b'}});
  assert.equal(scans.length, 2);
  scans = applyScanEvent(scans, {type:'scan_removed',scan_id:'a'});
  assert.equal(scans.length, 1); assert.equal(scans[0].id, 'b');
});
test('verification patches preserve fields not supplied by the worker', () => {
  const [scan] = applyScanEvent([{id:'a',gtin:'gtin',box_quantity:10,is_box:false,error_message:'old'}],
    {type:'scan_update',scan_id:'a',status:'valid',gtin:null,box_quantity:null,is_box:null,error_message:null});
  assert.equal(scan.status,'valid'); assert.equal(scan.gtin,'gtin'); assert.equal(scan.box_quantity,10);
  assert.equal(scan.is_box,false); assert.equal(scan.error_message,null);
});
test('events received during a snapshot request are replayed so old HTTP data cannot resurrect a deleted mark', () => {
  const queued = [{type:'scan_removed',scan_id:'old'},{type:'scan_upsert',scan:{id:'new'}}];
  const merged = queued.reduce(applyScanEvent,[{id:'old'}]);
  assert.equal(merged.length,1); assert.equal(merged[0].id,'new');
  assert.equal(applyScanEvent(merged,{type:'scans_reset'}).length,0);
});
function live(terminal) {
  let cleanup;
  const sockets=[],intervals=[],timers=[],received=[],listeners={};let reconciles=0;
  class Socket {
    static OPEN=1;
    constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this);}
    send(value){this.sent.push(value);}
    close(){this.readyState=3;this.onclose?.();}
  }
  const {useDocumentLive}=load('hooks/useDocumentLive.ts',{
    require(name){if(name==='react')return{useRef:value=>({current:value}),useEffect:fn=>{cleanup=fn();}};
      return{decodeJwtSub:()=> 'account'};},
    WebSocket:Socket,localStorage:{getItem:()=> 'token'},location:{protocol:'https:',host:'test.local'},
    document:{visibilityState:'visible',addEventListener:(name,fn)=>{listeners[name]=fn;},removeEventListener:name=>{delete listeners[name];}},
    window:{addEventListener:(name,fn)=>{listeners[name]=fn;},removeEventListener:name=>{delete listeners[name];}},
    setInterval:fn=>{intervals.push(fn);return intervals.length;},clearInterval(){},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){}
  });
  useDocumentLive('doc',terminal,event=>received.push(event),()=>reconciles++);
  return{sockets,intervals,timers,received,listeners,get reconciles(){return reconciles;},cleanup};
}
test('desktop reconnects and reconciles lost events, with silent filtering of other documents', () => {
  const f=live(false);assert.ok(f.sockets[0].url.includes('/ws/account?token='));
  f.sockets[0].onopen();assert.equal(f.reconciles,1);
  f.sockets[0].onmessage({data:JSON.stringify({type:'scan_removed',document_id:'other'})});
  assert.equal(f.received.length,0);
  f.sockets[0].onmessage({data:JSON.stringify({type:'scan_removed',document_id:'doc'})});
  assert.equal(f.received.length,1);
  f.intervals[0]();assert.equal(f.sockets[0].sent[0],'ping');
  f.sockets[0].close();assert.equal(f.reconciles,2);
  f.timers[0]();assert.equal(f.sockets.length,2);
  f.sockets[1].onopen();assert.equal(f.reconciles,3);
  f.cleanup();assert.equal(f.timers.length,1);assert.deepEqual(Object.keys(f.listeners),[]);
});
test('terminal uses a document-scoped socket and recovers progress after network/visibility changes', () => {
  const f=live(true);assert.ok(f.sockets[0].url.includes('/ws/tsd/doc?token='));
  f.listeners.online();f.listeners.visibilitychange();f.intervals[1]();
  assert.equal(f.reconciles,3);f.cleanup();
});
