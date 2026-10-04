const {test}=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'), vm=require('node:vm'), path=require('node:path'), ts=require('../node_modules/typescript');
const exportsObject={};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/lib/workplaceMode.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{exports:exportsObject});
const {documentWorkplaces,preferredWorkplace}=exportsObject;
const workplace=(id,stores=[],extra={})=>({id,store_ids:stores,is_active:true,scan_mode:'com',...extra});
test('document selects only its organization and active workplaces for its store',()=>{
 const profiles=[{id:'p',workplaces:[workplace('all'),workplace('match',['s'],{scan_mode:'tsd'}),workplace('other',['else']),workplace('inactive',['s'],{is_active:false})]},{id:'other',workplaces:[workplace('foreign',['s'])]}];
 assert.equal(documentWorkplaces(profiles,{organization_profile_id:'p',moysklad_store_id:'s'}).map(w=>w.id).join(','),'match');
 assert.equal(documentWorkplaces(profiles,{organization_profile_id:'p',moysklad_store_id:'unknown'}).map(w=>w.id).join(','),'all');
 assert.equal(documentWorkplaces(profiles,{organization_profile_id:'missing'}).length,0);
});
test('multiple applicable workplaces require a choice unless a default exists',()=>{
 assert.equal(preferredWorkplace([workplace('a'),workplace('b')]),undefined);
 assert.equal(preferredWorkplace([workplace('a'),workplace('b',[],{is_default:true})]).id,'b');
 assert.equal(preferredWorkplace([workplace('a')]).id,'a');
});
