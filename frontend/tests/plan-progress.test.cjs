const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {createRequire} = require('node:module');
const frontend = path.resolve(__dirname, '..');
const requireFrontend = createRequire(path.join(frontend, 'package.json'));
const ts = requireFrontend('typescript');
const source = fs.readFileSync(path.join(frontend, 'src/store/scanStore.ts'), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const exposed = {};
vm.runInNewContext(compiled, {exports:exposed,require:requireFrontend,setTimeout});
const {buildProgress,findProgressRowForScan} = exposed;

test('viewing positions leaves scanner automatic and preserves explicit manual mode',()=>{
  const store=exposed.useScanStore;
  store.getState().setTargetProductId(null);
  store.getState().togglePositionSelection({productId:'flavor-1',gtinKey:'04660515330359'});
  assert.equal(store.getState().targetProductId,null);
  store.getState().setTargetProductId('manual-flavor');
  store.getState().togglePositionSelection({productId:'flavor-2',gtinKey:'04660515330496'});
  assert.equal(store.getState().targetProductId,'manual-flavor');
  store.getState().setTargetProductId(null);
});

test('three flavor packs contribute ten units each',()=>{
  const gtins=['04660515330359','04660515330496','04660515330571'];
  const plan=gtins.map((gtin,i)=>({product_id:'p'+i,gtin:null,pack_gtins:[gtin],product_name:'p'+i,expected_qty:10}));
  const scans=gtins.map((gtin,i)=>({id:'s'+i,gtin,moysklad_product_id:'p'+i,status:'scanned',box_quantity:10}));
  const result=buildProgress(plan,scans);
  assert.equal(result.total.addedTotal,30);
  assert.deepEqual(Array.from(result.rows,row=>row.addedTotal),[10,10,10]);
});
const item = (pid, gtins) => ({product_id:pid,gtin:'02000000091327',gtins,product_name:pid,expected_qty:2});
const scan = (gtin,pid=null,status='scanned') => ({id:gtin,code:'01'+gtin+'21TEST',gtin,moysklad_product_id:pid,status});

test('two marks match second barcode and update the exact TSD position',()=>{
  const s=scan('04620543080527');
  const result=buildProgress([item('monster',['04620543080527'])],[s,{...s,id:'second'}]);
  assert.equal(result.total.addedTotal,2);
  assert.equal(findProgressRowForScan(s,result.rows).product_id,'monster');
});
test('True matches plan alias despite old out-of-plan Hard product id',()=>{
  const result=buildProgress([item('true',['04620164405358'])],[scan('04620164405358','hard')]);
  assert.equal(result.rows[0].addedTotal,1);
});
test('same GTIN on two products is not counted twice or assigned arbitrarily',()=>{
  const plan=[item('true',['04620164405358']),item('hard',['04620164405358'])];
  assert.equal(buildProgress(plan,[scan('04620164405358')]).total.addedTotal,0);
  const resolved=buildProgress(plan,[scan('04620164405358','true')]);
  assert.equal(resolved.total.addedTotal,1);
  assert.equal(resolved.rows[1].addedTotal,0);
});
test('invalid marks do not increase progress and removed marks reduce it',()=>{
  const plan=[item('monster',['04620543080527'])];
  assert.equal(buildProgress(plan,[scan('04620543080527',null,'invalid')]).total.addedTotal,0);
  assert.equal(buildProgress(plan,[]).total.addedTotal,0);
});

test('manual position wins over a GTIN from another row',()=>{
  const plan=[item('true',['04620164405358']),item('chosen',['04620543080503'])];
  const s=scan('04620164405358','chosen');
  const result=buildProgress(plan,[s]);
  assert.equal(findProgressRowForScan(s,result.rows).product_id,'chosen');
  assert.equal(result.rows[0].addedTotal,0);
  assert.equal(result.rows[1].addedTotal,1);
});
