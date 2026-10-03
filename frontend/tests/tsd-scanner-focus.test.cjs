const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/tsdScannerFocus.ts'), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
function setup() {
  const handlers={},timers=new Map(),appended=[],focusCalls=[];
  let timerId=0,blocked=false,selection='';
  const events={addEventListener(name,fn){handlers[name]=fn;},removeEventListener(name){delete handlers[name];}};
  class Element {constructor(tag='BUTTON'){this.tagName=tag;this.isContentEditable=false;}}
  const button=new Element();
  const doc={...events,visibilityState:'visible',activeElement:button};
  const input=new Element('INPUT');input.focus=(opts)=>{focusCalls.push(opts);doc.activeElement=input;};
  const win={...events,setTimeout(fn){timers.set(++timerId,fn);return timerId;},clearTimeout(id){timers.delete(id);},getSelection(){return {toString:()=>selection};}};
  const exposed={};vm.runInNewContext(compiled,{exports:exposed,window:win,document:doc,HTMLElement:Element});
  const cleanup=exposed.bindTsdScannerFocus({input:()=>input,blocked:()=>blocked,append:value=>appended.push(value)});
  function key(key,extra={}){const e={target:doc.activeElement,key,preventDefault(){this.prevented=true;},...extra};handlers.keydown(e);return e;}
  return {handlers,doc,input,button,appended,focusCalls,cleanup,key,Element,
    block(value){blocked=value;},selection(value){selection=value;},flush(){for(const fn of timers.values())fn();timers.clear();}};
}
test('first scanner character is recovered from a button without page scrolling',()=>{
  const f=setup();f.doc.activeElement=f.button;
  assert.equal(f.key('0').prevented,true);assert.deepEqual(f.appended,['0']);
  assert.equal(f.doc.activeElement,f.input);assert.equal(f.focusCalls.at(-1).preventScroll,true);
  assert.equal(f.key('1').prevented,undefined);assert.deepEqual(f.appended,['0']);
  f.cleanup();assert.deepEqual(Object.keys(f.handlers),[]);
});
test('IME focus returns after touching order controls and pasted GS is preserved',()=>{
  const f=setup();f.doc.activeElement=f.button;f.handlers.pointerup();f.flush();
  assert.equal(f.doc.activeElement,f.input);
  f.doc.activeElement=f.button;const code='010462054308052721TEST\x1d93TEST';
  const event={target:f.button,clipboardData:{getData:()=>code},preventDefault(){this.prevented=true;}};
  f.handlers.paste(event);assert.equal(event.prevented,true);assert.deepEqual(f.appended,[code]);
});
test('pending requests, text selection, other editors and keyboard navigation are protected',()=>{
  const f=setup();f.doc.activeElement=f.button;f.block(true);
  assert.equal(f.key('0').prevented,undefined);f.handlers.pointerup();f.flush();assert.equal(f.doc.activeElement,f.button);
  f.block(false);f.selection('selected mark');f.handlers.pointerup();f.flush();assert.equal(f.doc.activeElement,f.button);
  assert.equal(f.key('Tab').prevented,undefined);assert.equal(f.key('Enter').prevented,undefined);
  assert.equal(f.key('c',{ctrlKey:true}).prevented,undefined);
  f.doc.activeElement=new f.Element('TEXTAREA');assert.equal(f.key('0').prevented,undefined);
  assert.deepEqual(f.appended,[]);
});
