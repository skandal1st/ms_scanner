const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '..'), req = createRequire(path.join(root, 'package.json'));
const ts = req('typescript'), exposed = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, 'src/store/scanStore.ts'), 'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,
  {exports:exposed,require:req});
const store = exposed.useScanStore;
const first = {id:'first',document_id:'doc',code:'first',status:'valid',gtin:'04620543080527'};

test('repeat reveals existing mark without adding units or changing scanner target', () => {
  store.getState().reset();
  store.getState().addScan(first);
  store.getState().setSelection({productId:'other',gtinKey:null});
  store.getState().setTargetProductId('other');
  store.getState().addScan({...first,duplicate:true});
  store.getState().flashScan(first.id);
  const state = store.getState();
  assert.equal(state.scans.length,1);
  assert.equal(state.stats.valid,1);
  assert.equal(state.flashScanId,first.id);
  assert.equal(state.selection,null);
  assert.equal(state.targetProductId,'other');
  const version = state.flashScanVersion;
  store.getState().flashScan(first.id);
  assert.equal(store.getState().flashScanVersion,version+1);
});

test('live refresh preserves highlight; next scan and changing document clear it', () => {
  store.getState().setScans([first]);
  store.getState().flashScan(first.id);
  store.getState().setScans([{...first,product_name:'Обновлённое имя'}]);
  assert.equal(store.getState().flashScanId,first.id);
  store.getState().addScan({...first,id:'next',code:'next'});
  assert.equal(store.getState().flashScanId,null);
  store.getState().flashScan(first.id);
  store.getState().setDocument({id:'other-document'});
  assert.equal(store.getState().flashScanId,null);
  store.getState().flashScan(first.id);
  store.getState().reset();
  assert.equal(store.getState().flashScanId,null);
});
