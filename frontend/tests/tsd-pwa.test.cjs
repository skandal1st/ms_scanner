const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(process.env.TSD_SW_PATH || 'dist/tsd-sw.js','utf8');
function worker(offline=false) {
 const handlers={}, fetched=[], matched=[], cached=[];
 const cache={addAll:async files=>cached.push(...files),match:async key=>{matched.push(key);return 'cached-shell'}};
 vm.runInNewContext(source,{self:{location:{origin:'https://test.invalid'},addEventListener:(name,fn)=>handlers[name]=fn},URL,
  caches:{open:async()=>cache,keys:async()=>[],delete:async()=>true},
  fetch:async request=>{fetched.push(request);if(offline)throw Error('offline');return 'network-response'}});
 return {handlers,fetched,matched,cached};
}
test('never intercepts API responses or scan writes',()=>{
 const w=worker();for(const [method,path] of [['GET','/api/tsd/documents'],['POST','/api/tsd/documents/x/scans'],['GET','/ws/x']]){
  let intercepted=false;w.handlers.fetch({request:{method,url:'https://test.invalid'+path},respondWith:()=>intercepted=true});assert.equal(intercepted,false);
 }
});
test('offline document link falls back to public shell without storing its query',async()=>{
 const w=worker(true);let response;w.handlers.fetch({request:{method:'GET',mode:'navigate',url:'https://test.invalid/tsd?pair=secret-once'},respondWith:p=>response=p});
 assert.equal(await response,'cached-shell');assert.deepEqual(w.fetched,['/tsd']);assert.deepEqual(w.matched,['/tsd']);
});
test('precache contains shell and built assets, never user API data',async()=>{
 const w=worker();let installed;w.handlers.install({waitUntil:p=>installed=p});await installed;
 assert.ok(w.cached.includes('/tsd'));assert.ok(w.cached.some(path=>path.startsWith('/assets/')&&path.endsWith('.js')));
 assert.ok(w.cached.every(path=>!path.includes('/api/')&&!path.includes('?')));
});
