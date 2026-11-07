const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path')
const ts = require('../node_modules/typescript')
const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/lib/clientTimezone.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText

test('timezone follows the device at action time instead of a cached page value', () => {
  let zone = 'Asia/Vladivostok'
  const result = {}
  vm.runInNewContext(code, { exports: result, Intl: { DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: zone }) }) } })
  assert.equal(result.getClientTimezone(), 'Asia/Vladivostok')
  zone = 'America/New_York'
  assert.equal(result.getClientTimezone(), 'America/New_York')
})

test('old devices can use a configured zone when auto detection is unavailable', () => {
  const result = {}
  vm.runInNewContext(code, { exports: result, Intl: { DateTimeFormat: () => { throw new Error('unsupported') } } })
  assert.equal(result.getClientTimezone(), null)
  const options = Array.from(result.timezoneOptions(['America/New_York', 'Asia/Omsk']))
  assert.ok(options.includes('Europe/Moscow'))
  assert.ok(options.includes('Asia/Vladivostok'))
  assert.equal(options.filter(zone => zone === 'Asia/Omsk').length, 1)
  assert.ok(options.includes('America/New_York'))
})

test('Android without Intl timezone data sends its UTC offset, including fractional hours', () => {
  let offset = -345
  class DeviceDate extends Date { getTimezoneOffset() { return offset } }
  const result = {}
  vm.runInNewContext(code, { exports: result, Date: DeviceDate, Intl: { DateTimeFormat: () => { throw new Error('unsupported') } } })
  assert.equal(result.getClientTimezoneContext().timezone, null)
  assert.equal(result.getClientTimezoneContext().utc_offset_minutes, 345)
  offset = 300
  assert.equal(result.getClientTimezoneContext().utc_offset_minutes, -300)
  offset = NaN
  assert.equal(result.getClientTimezoneContext().utc_offset_minutes, null)
})
