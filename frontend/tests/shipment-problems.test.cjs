const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const root=path.resolve(__dirname,'..'),req=createRequire(path.join(root,'package.json')),ts=req('typescript');
function load(file,dependencies={}) {
  const exports={};
  const compiled=ts.transpileModule(fs.readFileSync(path.join(root,file),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  vm.runInNewContext(compiled,{exports,require:name=>dependencies[name]||req(name),setTimeout});return exports;
}
const store=load('src/store/scanStore.ts');
const {isProblemScan,numberedScans,problemPositionRows}=load('src/lib/shipmentProblems.ts',{'../store/scanStore':store});
const scan=(id,status='valid',extra={})=>({id,status,code:'code'+id,gtin:'04620543080527',scanned_at:`2026-10-23T10:00:0${id}Z`,...extra});

test('warnings include rejected, withdrawn, MS and child errors, ownership and visible repeat',()=>{
  for(const status of ['invalid','duplicate','overflow','unknown_product','used_in_other_doc']) assert(isProblemScan(scan('1',status)));
  for(const extra of [{withdrawn:true},{verification:{ms_error:'error'}},{verification:{child_issues:[{}]}},{verification:{owner_result:'unknown'}}]) assert(isProblemScan(scan('1','valid',extra)));
  assert(isProblemScan(scan('1','valid',{owner_inn:'other'}),'ours'));
  assert(isProblemScan(scan('1'),null,'1'));
  for(const status of ['valid','pending','scanned']) assert.equal(isProblemScan(scan('1',status)),false);
  assert(isProblemScan(scan('1'),null,null,[{gtin:'00000000000002',product_id:'other'}]));
});
test('numbering survives input reorder, status update and filtering without mutating scans',()=>{
  const scans=[scan('3'),scan('1'),scan('2','invalid')];
  const rows=numberedScans(scans);
  assert.equal(rows.map(r=>r.number).join(','),'3,2,1');
  assert.equal(rows.filter(r=>isProblemScan(r.scan))[0].number,2);
  assert.equal(numberedScans([...scans].reverse().map(s=>({...s,status:'valid'}))).map(r=>r.scan.id).join(','),'3,2,1');
  assert.equal(scans.map(s=>s.id).join(','),'3,1,2');
});
test('positions keep shortage, excess and mark errors while hiding completed healthy positions',()=>{
  const rows=['healthy','short','over','bad'].map((product_id,i)=>({product_id,gtin:String(i),product_name:product_id,expected:2,scanned:2,addedTotal:i===1?0:i===2?3:2}));
  const scans=[scan('1','invalid',{moysklad_product_id:'bad'})];
  const progress={hasPlan:true,rows,offPlanRows:[]};
  const result=problemPositionRows(progress,scans);
  assert.equal(result.map(r=>r.product_id).join(','),'short,over,bad');
  assert.equal(rows[0].addedTotal,2);
});
test('rejected-only positions remain available and off-plan rows are not duplicated',()=>{
  const scans=[scan('1','invalid',{gtin:'1'}),scan('2','invalid',{gtin:'1'}),scan('3','overflow',{gtin:'2'})];
  const result=problemPositionRows({hasPlan:true,rows:[],offPlanRows:[{scanIds:['3']}]},scans);
  assert.equal(result.length,1);
  assert.equal(result[0].addedTotal,0);
});
