const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const file = path.join(__dirname, '../src/lib/serialLineBuffer.ts')
const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
})
const lib = {}
vm.runInNewContext(compiled.outputText, { exports: lib, TextDecoder, Uint8Array })

test('COM evidence belongs to its line across chunk and CRLF boundaries', () => {
  const first = Buffer.from('010462718329680621zNGSda7\xf893ryrd', 'latin1')
  const second = Buffer.from('010461027056175821gatN9\x1d93nb7l', 'latin1')
  const stream = Buffer.concat([first, Buffer.from('\r\n'), second, Buffer.from('\n')])
  for (let cut = 0; cut <= stream.length; cut++) {
    const buffer = new lib.SerialLineBuffer()
    const frames = [...buffer.feed(stream.subarray(0, cut)), ...buffer.feed(stream.subarray(cut))]
    assert.deepEqual(frames.map(frame => frame.rawHex), [first.toString('hex'), second.toString('hex')])
    assert.equal(lib.normalizeGs(frames[0].line), '010462718329680621zNGSda7\x1d93ryrd')
    assert.equal(lib.normalizeGs(frames[1].line), second.toString('latin1'))
  }
})

test('oversized frames still deliver the code and leave the next capture intact', () => {
  const [long, next] = new lib.SerialLineBuffer().feed(Buffer.from('a'.repeat(4097) + '\rGS\r'))
  assert.equal(long.line.length, 4097)
  assert.equal(long.rawHex, undefined)
  assert.equal(next.rawHex, '4753')
})
