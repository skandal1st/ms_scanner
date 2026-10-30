const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('../node_modules/typescript')
const axios = require('../node_modules/axios/dist/node/axios.cjs')
const { QueryClient } = require('../node_modules/@tanstack/react-query')

const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/lib/tsdDocumentLifecycle.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText
const exported = {}
vm.runInNewContext(source, { exports: exported, require: () => axios })
const { retryTsdDocument, isTsdDocumentClosed } = exported
const httpError = status => new axios.AxiosError('Request failed', undefined, undefined, undefined, { status })

for (const status of [404, 409]) test(`document ${status} is terminal and is not retried`, async () => {
  const client = new QueryClient()
  let requests = 0
  try {
    await assert.rejects(client.fetchQuery({ queryKey: ['tsd-document', 'closed'],
      queryFn: async () => { requests++; throw httpError(status) }, retry: retryTsdDocument, retryDelay: 0 }),
    error => isTsdDocumentClosed(error))
    assert.equal(requests, 1)
  } finally { client.clear() }
})

for (const failure of [httpError(503), new axios.AxiosError('Network error')]) test(`${failure.response?.status || 'network'} allows recovery of the same shipment`, async () => {
  const client = new QueryClient()
  let requests = 0
  try {
    const result = await client.fetchQuery({ queryKey: ['tsd-document', 'active'],
      queryFn: async () => { if (++requests < 2) throw failure; return { id: 'active', status: 'draft' } },
      retry: retryTsdDocument, retryDelay: 0 })
    assert.equal(result.id, 'active')
    assert.equal(requests, 2)
    assert.equal(isTsdDocumentClosed(failure), false)
  } finally { client.clear() }
})

test('authentication and malformed errors do not discard a shipment', () => {
  for (const error of [httpError(401), httpError(403), new Error('404'), null]) {
    assert.equal(isTsdDocumentClosed(error), false)
  }
})
