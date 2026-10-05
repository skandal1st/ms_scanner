import api, { tsdClient } from './client'
export type CountMode = 'acceptance' | 'inventory'
export interface CountRow { key: string; product_name: string; gtin?: string; gtins?: string[]; folder_name: string; expected_qty: number; base_qty: number; shipment_qty: number }
export interface CountScan { id: string; device_id: string; code: string; product_key: string; quantity: number }
export interface CountSummary {
  id: string; mode: CountMode; name: string; status: string; created_at: string; error_message?: string
  settings: { store_name?: string; snapshot_at?: string; reviewed_brands?: string[]; included_states?: { id: string; name: string }[] }
}
export interface CountProgress extends CountSummary { counts: Record<string, number>; scans: CountScan[]; duplicate?: boolean; product_key?: string }
export interface CountDetail extends CountProgress { plan: CountRow[] }
export interface CountCreate { mode: CountMode; document_id?: string; store_id?: string; include_state_ids?: string[] }
export function countApi(terminal: boolean) {
  const client = terminal ? tsdClient : api
  const root = terminal ? '/tsd/counts' : '/physical-counts'
  return {
    list: (mode: CountMode, document_id?: string) => client.get<CountSummary[]>(root, { params: { mode, document_id } }),
    create: (body: CountCreate) => client.post<CountSummary>(root, body),
    options: () => client.get<{ stores: { id: string; name: string }[]; states: { id: string; name: string }[] }>(root + '/options'),
    acceptances: () => client.get<{ id: string; name: string; created_at: string; positions: number }[]>('/tsd/acceptances'),
    detail: (id: string) => client.get<CountDetail>(`${root}/${id}`),
    progress: (id: string) => client.get<CountProgress>(`${root}/${id}/progress`),
    scan: (id: string, code: string, product_key?: string, quantity = 1, brand?: string, request_id: string = crypto.randomUUID()) => client.post<CountProgress>(`${root}/${id}/scans`, { code, product_key, quantity, brand, request_id }),
    remove: (id: string, scanId: string) => client.delete<CountProgress>(`${root}/${id}/scans/${scanId}`),
    brand: (id: string, brand: string) => client.post<CountProgress>(`${root}/${id}/brands/complete`, { brand }),
    complete: (id: string) => client.post<CountSummary>(`${root}/${id}/complete`),
    async export(id: string) {
      const { data } = await client.get(`${root}/${id}/export`, { responseType: 'blob' })
      const url = URL.createObjectURL(data)
      const link = document.createElement('a'); link.href = url; link.download = `count-${id}.csv`; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  }
}
