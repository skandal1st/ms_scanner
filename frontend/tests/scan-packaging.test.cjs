const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '..');
const req = createRequire(path.join(root, 'package.json'));
const ts = req('typescript');
const source = fs.readFileSync(path.join(root, 'src/lib/scanPackaging.ts'), 'utf8');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,
  {exports:exportsObject, require:req});
const {scanPackageLabel,scanPackageType} = exportsObject;
test('pack GTIN displays a block of ten independently of the SSCC flag',()=>{
 const scan={package_type:'GROUP',is_box:false,box_quantity:10,keep_aggregate:true};
 assert.equal(scanPackageType(scan),'GROUP');
 assert.equal(scanPackageLabel(scan),'Блок · 10 шт. · целиком');
});
test('expanded block keeps its type and quantity, only its write mode changes',()=>{
 assert.equal(scanPackageLabel({package_type:'GROUP',box_quantity:2,child_codes:['a','b'],keep_aggregate:false}),
  'Блок · 2 шт. · вложенные марки');
});
test('transport boxes, unit marks and unmarked barcode quantities remain distinct',()=>{
 assert.equal(scanPackageLabel({is_box:true,box_quantity:20}),'Короб · 20 шт. · целиком');
 assert.equal(scanPackageLabel({}),'Отдельная марка · 1 шт.');
 assert.equal(scanPackageLabel({is_barcode:true,box_quantity:10}),'Штрихкод · 10 шт.');
});
