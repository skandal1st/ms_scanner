const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '..');
const ts = createRequire(path.join(root, 'package.json'))('typescript');
const source = fs.readFileSync(path.join(root, 'src/lib/shipmentLabel.ts'), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const exposed = {};
vm.runInNewContext(compiled, {exports:exposed});
test('order is parenthesized, shipment and counterparty follow it', () => {
  assert.equal(exposed.shipmentLabel({name:'0052',customer_order_name:'0042',agent_name:'ООО Покупатель'}), '(0042) 0052 ООО Покупатель');
  assert.equal(exposed.shipmentLabel({name:'0052',agent_name:'ООО Покупатель'}), '0052 ООО Покупатель');
  assert.equal(exposed.shipmentLabel({name:'0052'}), '0052');
});
