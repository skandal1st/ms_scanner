const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/tsdSound.ts'), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
function fixture(supported=true, reject=false, failClip=false) {
  const tones=[], contexts=[], clips=[], requests=[];
  class Context {
    constructor(){this.state='suspended';this.currentTime=10;contexts.push(this);}
    async resume(){if(reject)throw Error('blocked');this.state='running';}
    async close(){this.state='closed';}
    async decodeAudioData(){if(failClip)throw Error('decode failed');return {decoded:true};}
    createBufferSource(){const clip={connect(){},disconnect(){},start(){this.started=true;},stop(){this.stopped=true;}};clips.push(clip);return clip;}
    createOscillator(){const tone={frequency:{},connect(){},disconnect(){},start(t){this.startAt=t;},stop(t){this.stopAt=t;}};tones.push(tone);return tone;}
    createGain(){return {connect(){},disconnect(){},gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}}};}
  }
  const fetch=async url=>{requests.push(url);return {ok:true,arrayBuffer:async()=>new ArrayBuffer(8)};};
  const exports={};vm.runInNewContext(compiled,{exports,fetch,window:supported?{AudioContext:Context}:{}});
  return {sound:exports.createTsdSound(),tones,contexts,clips,requests};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
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

test('only cross-document conflict plays the supplied clip, loaded once',async()=>{
  const {sound,tones,clips,requests}=fixture();
  sound.play('used_in_other_doc');assert.equal(clips.length,0);assert.equal(requests.length,0);
  await sound.activate();await settle();
  sound.play('ok');sound.play('error');assert.equal(clips.length,0);
  sound.play('used_in_other_doc');await settle();
  assert.equal(clips.length,1);assert.equal(clips[0].started,true);
  assert.equal(tones.length,3);
  sound.play('used_in_other_doc');await settle();
  assert.equal(clips[0].stopped,true);assert.equal(clips.length,2);
  assert.deepEqual(requests,['/sounds/used-in-other-document.mp3']);
  sound.stop();assert.equal(clips[1].stopped,true);
});

test('broken MP3 falls back to the existing error beep',async()=>{
  const {sound,tones,clips}=fixture(true,false,true);
  await sound.activate();await settle();
  sound.play('used_in_other_doc');await settle();
  assert.equal(clips.length,0);
  assert.deepEqual(tones.map(t=>t.frequency.value),[220,220]);
});

test('closing cancels a pending conflict playback',async()=>{
  const {sound,clips}=fixture();
  await sound.activate();sound.play('used_in_other_doc');sound.close();await settle();
  assert.equal(clips.length,0);
});
