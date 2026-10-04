const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const ts = require('../node_modules/typescript');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(require('node:path').join(__dirname, '../src/lib/tsdLinks.ts'), 'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText, {exports:exportsObject, URL});
const {tsdDocumentLink,parseTsdDocumentCode,TSD_APK_PATH} = exportsObject;
const id = '11111111-1111-4111-8111-111111111111';
test('shipment QR works as an Android link and as scanned input',()=>{
  assert.equal(tsdDocumentLink(id),'skandata:'+id);
  assert.equal(parseTsdDocumentCode(' '+tsdDocumentLink(id)+'\n'),id);
});
test('existing PWA and old document codes still work',()=>{
  assert.equal(parseTsdDocumentCode('https://skandata.ru/tsd?document='+id),id);
  assert.equal(parseTsdDocumentCode('SKANDATA:DOCUMENT:'+id),id);
});
test('malformed application links and pairing QR cannot open a shipment',()=>{
  for(const value of ['skandata:javascript:alert(1)','skandata:'+id+'#bad','SKANDATA:TSD:pair','https://skandata.ru/tsd?pair=secret'])
    assert.equal(parseTsdDocumentCode(value),null);
  assert.equal(TSD_APK_PATH,'/downloads/skandata-tsd.apk');
});
