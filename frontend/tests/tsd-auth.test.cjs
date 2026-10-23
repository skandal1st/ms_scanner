const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')
const ts = require('../node_modules/typescript')
const axios = require('../node_modules/axios/dist/node/axios.cjs')

const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/lib/tsdAuth.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText

function harness({ legacy = false, failure, offline = false, deny = false } = {}) {
  const values = new Map([['tsd_access_token', 'old'], ['tsd_document_id', 'document']])
  if (!legacy) values.set('tsd_refresh_token', 'binding')
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
  const window = { location: { href: '/tsd?document=example' } }
  const sent = []
  let refreshes = 0
  const client = axios.create({ adapter: async config => {
    sent.push(config)
    const response = { config, status: 200, statusText: 'OK', data: 'ok', headers: {} }
    if (deny || config.headers.Authorization !== 'Bearer new') {
      response.status = 401
      throw new axios.AxiosError('Expired', 'ERR_BAD_REQUEST', config, null, response)
    }
    return response
  } })
  const mock = { ...axios, post: async (url, body) => {
    refreshes++
    assert.equal(url, '/api/tsd/auth/refresh')
    assert.equal(body.refresh_token, legacy ? 'old' : 'binding')
    await new Promise(resolve => setTimeout(resolve, 5))
    if (failure) throw new axios.AxiosError('Failed', 'ERR_BAD_REQUEST', undefined, null,
      failure === 'network' ? undefined : { status: failure })
    return { data: { access_token: 'new', refresh_token: 'renewed-binding' } }
  } }
  const exports = {}
  vm.runInNewContext(source, { exports, require: () => ({ __esModule: true, default: mock }), localStorage: storage,
    navigator: { onLine: !offline }, window })
  exports.installTsdAuth(client)
  return { client, values, window, sent, refreshes: () => refreshes }
}

test('expired shift renews once across concurrent requests and preserves the document', async () => {
  const h = harness()
  const results = await Promise.all([h.client.get('/tsd/me'), h.client.get('/tsd/orders'), h.client.post('/tsd/documents/doc/scans', { code: 'mark' })])
  assert.equal(h.refreshes(), 1)
  assert.ok(results.every(r => r.data === 'ok'))
  assert.equal(h.values.get('tsd_refresh_token'), 'renewed-binding')
  assert.equal(h.values.get('tsd_document_id'), 'document')
  assert.equal(h.window.location.href, '/tsd?document=example')
  const scans = h.sent.filter(c => c.url.endsWith('/scans'))
  assert.equal(scans.length, 2)
  assert.equal(scans[0].data, scans[1].data)
})

test('legacy valid access token upgrades before sending requests', async () => {
  const h = harness({ legacy: true })
  await Promise.all([h.client.get('/tsd/me'), h.client.get('/tsd/orders')])
  assert.equal(h.refreshes(), 1)
  assert.equal(h.sent.length, 2)
})

test('stored binding can restore a missing access token', async () => {
  const h = harness()
  h.values.delete('tsd_access_token')
  await h.client.get('/tsd/me')
  assert.equal(h.refreshes(), 1)
  assert.equal(h.sent.length, 1)
  assert.equal(h.values.get('tsd_document_id'), 'document')
})

test('a rejected renewed token stops after one retry', async () => {
  const h = harness({ deny: true })
  await assert.rejects(h.client.get('/tsd/me'))
  assert.equal(h.refreshes(), 1)
  assert.equal(h.sent.length, 2)
  assert.equal(h.values.size, 0)
})

for (const failure of ['network', 403]) test(`refresh failure ${failure} preserves binding and active document`, async () => {
  const h = harness({ failure })
  await assert.rejects(h.client.get('/tsd/me'))
  assert.equal(h.refreshes(), 1)
  assert.equal(h.values.get('tsd_access_token'), 'old')
  assert.equal(h.values.get('tsd_refresh_token'), 'binding')
  assert.equal(h.values.get('tsd_document_id'), 'document')
  assert.equal(h.window.location.href, '/tsd?document=example')
})

test('revoked or expired binding returns to QR connection', async () => {
  const h = harness({ failure: 401 })
  await assert.rejects(h.client.get('/tsd/me'))
  assert.equal(h.refreshes(), 1)
  assert.equal(h.values.size, 0)
  assert.equal(h.window.location.href, '/tsd')
})

test('offline startup retains binding without attempting refresh', async () => {
  const h = harness({ legacy: true, offline: true })
  await assert.rejects(h.client.get('/tsd/me'))
  assert.equal(h.refreshes(), 0)
  assert.equal(h.values.get('tsd_access_token'), 'old')
})
