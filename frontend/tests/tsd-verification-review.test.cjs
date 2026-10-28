const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const root=path.resolve(__dirname,'..'),req=createRequire(path.join(root,'package.json')),ts=req('typescript');
function load(file,dependencies={}) {
 const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root,file),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,
  {exports,require:name=>dependencies[name]||req(name),setTimeout});return exports;
}
const store=load('src/store/scanStore.ts');
const problems=load('src/lib/shipmentProblems.ts',{'../store/scanStore':store});
const {verificationReview,verificationStamp,verificationResultsArrived}=load('src/lib/tsdVerificationReview.ts',{'../store/scanStore':store,'./shipmentProblems':problems});
const mark=(id,patch={})=>({id,code:'010463033960870521TEST'+id,gtin:'04630339608705',status:'valid',verification:{source:'cz_info',checked_at:'2026-10-28T10:00:00Z',owner_result:'match'},...patch});

test('worker failed=0 never hides a rejected red mark',()=>{
 const result=verificationReview([mark('ok'),mark('bad',{status:'invalid'})],[],0);
 assert.equal(result.hasIssues,true);assert.equal(result.attention,1);assert.match(result.text,/1 из 2/);
});
test('healthy completed check gives explicit all-clear and ignores unmarked barcodes',()=>{
 const result=verificationReview([mark('ok'),mark('barcode',{is_barcode:true})],[],0);
 assert.equal(result.hasIssues,false);assert.equal(result.total,1);assert.match(result.text,/Проблем не найдено/);
});
test('unchecked marks and failed checks remain attention items',()=>{
 const result=verificationReview([mark('unknown',{status:'scanned',verification:null})],[],1);
 assert.equal(result.hasIssues,true);assert.equal(result.unchecked,1);assert.equal(result.attention,1);
 assert.match(result.text,/Не удалось проверить: 1/);
});
test('withdrawn, wrong owner, overflow and missing planned product require attention',()=>{
 for(const patch of [{withdrawn:true},{verification:{owner_result:'mismatch'}},{status:'overflow'}]) {
  assert.equal(verificationReview([mark('x',patch)],[]).attention,1);
 }
 const plan=[{gtin:'04640195516373',product_id:'other',product_name:'Other',expected_qty:1}];
 assert.equal(verificationReview([mark('x')],plan).attention,1);
});
test('snapshot fallback waits for new verification evidence for every target',()=>{
 const old=[mark('a'),mark('b')];const baseline=new Map(old.map(s=>[s.id,verificationStamp(s)]));
 assert.equal(verificationResultsArrived(baseline,old),false);
 const fresh=mark('a',{verified_at:'new'});
 assert.equal(verificationResultsArrived(baseline,[fresh,old[1]]),false);
 assert.equal(verificationResultsArrived(baseline,[fresh,mark('b',{verified_at:'new'})]),true);
 assert.equal(verificationResultsArrived(baseline,[fresh]),true);
 assert.equal(verificationResultsArrived(baseline,[fresh,mark('b',{status:'pending',verification:{source:'packaging_pending'}})]),false);
 assert.equal(verificationResultsArrived(new Map(),old),false);
});
