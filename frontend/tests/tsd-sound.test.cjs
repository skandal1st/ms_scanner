const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/tsdSound.ts'), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
function fixture(supported=true, reject=false) {
  const tones=[], contexts=[];
  class Context {
    constructor(){this.state='suspended';this.currentTime=10;contexts.push(this);}
    async resume(){if(reject)throw Error('blocked');this.state='running';}
    async close(){this.state='closed';}
    createOscillator(){const tone={frequency:{},connect(){},disconnect(){},start(t){this.startAt=t;},stop(t){this.stopAt=t;}};tones.push(tone);return tone;}
    createGain(){return {connect(){},disconnect(){},gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}}};}
  }
  const exports={};vm.runInNewContext(compiled,{exports,window:supported?{AudioContext:Context}:{}});
  return {sound:exports.createTsdSound(),tones,contexts};
}
test('audio waits for activation and success differs from double error',async()=>{
  const {sound,tones,contexts}=fixture();sound.play('ok');assert.equal(tones.length,0);
  assert.equal(await sound.activate(),true);await sound.activate();assert.equal(contexts.length,1);
  sound.play('ok');sound.play('error');
  assert.deepEqual(tones.map(t=>t.frequency.value),[880,220,220]);
  assert.deepEqual(tones.map(t=>t.startAt),[10,10,10.2]);
  sound.close();sound.play('ok');assert.equal(tones.length,3);
  assert.equal(await sound.activate(),true);assert.equal(contexts.length,2);
});
test('unsupported or blocked audio never interrupts scanning',async()=>{
  for(const f of [fixture(false),fixture(true,true)]){
    assert.equal(await f.sound.activate(),false);assert.doesNotThrow(()=>f.sound.play('error'));
    assert.equal(f.tones.length,0);
  }
});
