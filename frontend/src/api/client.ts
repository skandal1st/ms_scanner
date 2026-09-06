import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Единый refresh на параллельные 401: чтобы пачка одновременных запросов не
// дёргала /auth/refresh многократно. Раздаётся один промис на всех.
let refreshPromise: Promise<string | null> | null = null

async function tryRefresh(): Promise<string | null> {
  const rt = localStorage.getItem('refresh_token')
  if (!rt) return null
  try {
    // Прямой axios (не `api`) — минуем этот же интерцептор, без рекурсии на 401.
    const resp = await axios.post<{ access_token: string; refresh_token: string }>(
      '/api/auth/refresh',
      { refresh_token: rt },
    )
    localStorage.setItem('access_token', resp.data.access_token)
    if (resp.data.refresh_token) {
      localStorage.setItem('refresh_token', resp.data.refresh_token)
    }
    return resp.data.access_token
  } catch {
    return null
  }
}

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const original = error.config
    if (error.response?.status === 401 && original && !original._retry) {
      original._retry = true
      refreshPromise = refreshPromise ?? tryRefresh()
      const newToken = await refreshPromise
      refreshPromise = null
      if (newToken) {
        original.headers = original.headers ?? {}
        original.headers.Authorization = `Bearer ${newToken}`
        return api(original)
      }
      // Refresh не удался — сессия действительно закончилась.
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user_id')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default api

export type DocumentKind = 'demand' | 'loss' | 'supply'

export interface MsDocument {
  id: string
  name: string
  moment: string | null
  customer_order_name: string | null
  agent_name: string | null
}

export interface PlanItem {
  gtin: string | null
  product_id: string | null
  product_name: string
  expected_qty: number
  /** Маркированный товар (по trackingType МС). false/undefined — немаркированный (штрихкод). */
  marked?: boolean
}

export interface Document {
  id: string
  moysklad_id: string | null
  name: string
  kind: DocumentKind
  status: 'draft' | 'processing' | 'accepted'
  scan_count: number
  plan: PlanItem[]
  writeoff_reason?: string | null
  /** Причина неуспешной отправки в МС (напр. «нет на складе»), если была. */
  error_message?: string | null
  created_at: string
}

// ── Контроль марок (ЭДО Saby) ────────────────────────────────────────────────
export interface SabyStatus {
  connected: boolean
  mode?: 'service' | 'login' | null
  login?: string | null
  account?: string | null
  app_client_id?: string | null
}

export interface SabyConnectPayload {
  app_client_id?: string
  app_secret?: string
  secret_key?: string
  login?: string
  password?: string
  account?: string
}

export interface EdoDocRow {
  id?: string | null
  number?: string | null
  date?: string | null
  type?: string | null
  direction?: string | null
  counterparty_name?: string | null
  counterparty_inn?: string | null
  state_code?: number | null
  state_name?: string | null
  state_desc?: string | null
  incomplete?: boolean | null
  unsigned?: boolean | null
  note?: string | null
}

export interface EdoDocumentsResult {
  documents: EdoDocRow[]
  unsigned_count: number
}

export const markControlApi = {
  sabyStatus: () => api.get<SabyStatus>('/mark-control/saby/status'),
  sabyConnect: (payload: SabyConnectPayload) =>
    api.post<SabyStatus>('/mark-control/saby/connect', payload),
  sabyDocuments: (params: {
    direction?: string
    doc_type?: string
    date_from?: string
    date_to?: string
    page?: number
    page_size?: number
  }) => api.post<EdoDocumentsResult>('/mark-control/saby/documents', params),
  edoSync: (date_from: string, date_to?: string, use_cursor = false) =>
    api.post<{ status: string }>('/mark-control/edo/sync', { date_from, date_to, use_cursor }),
  edoBackfillNames: (days = 365) =>
    api.post<{ status: string; days: number }>('/mark-control/edo/backfill-names', { days }),
  edoSyncStatus: () =>
    api.get<{ running: boolean; result: EdoSyncResult | null; progress: EdoSyncProgress | null }>(
      '/mark-control/edo/sync/status',
    ),
  edoDocumentsDb: () => api.get<EdoDbDoc[]>('/mark-control/edo/documents-db'),
  edoStuck: () => api.get<EdoStuckResult>('/mark-control/edo/stuck'),
  czSnapshotRefresh: () => api.post<{ status: string }>('/mark-control/cz/snapshot/refresh'),
  czSnapshotStatus: () =>
    api.get<{ running: boolean; size: number; at: string | null; result: any }>(
      '/mark-control/cz/snapshot/status',
    ),
  edoStuckXlsx: () =>
    api.get('/mark-control/edo/stuck.xlsx', { responseType: 'blob' }),
}

export interface EdoStuckCounterparty {
  counterparty_inn?: string | null
  counterparty_name?: string | null
  not_accepted_upd: number
  marks_total: number
  stuck_marks: number
}

export interface EdoStuckDoc {
  number?: string | null
  doc_date?: string | null
  counterparty_inn?: string | null
  counterparty_name?: string | null
  state_name?: string | null
  total: number
  stuck: number
}

export interface EdoStuckResult {
  has_snapshot: boolean
  snapshot_size: number
  snapshot_at?: string | null
  counterparties?: EdoStuckCounterparty[]
  documents: EdoStuckDoc[]
  stuck_docs?: number
  stuck_marks?: number
}

export interface EdoSyncResult {
  pages: number
  documents: number
  out_realizations: number
  parsed_docs: number
  marks_saved: number
  names_saved?: number
}

export interface EdoSyncProgress {
  pages: number
  documents: number
  out_realizations: number
  parsed_docs: number
  marks_saved: number
  names_saved: number
  percent: number | null
  backfill: boolean
}

export interface EdoDbDoc {
  number?: string | null
  doc_date?: string | null
  counterparty_name?: string | null
  counterparty_inn?: string | null
  state_name?: string | null
  codes_total: number
  marks_parsed: boolean
}

// ── Инвентаризация: сверка остатков ЧЗ ↔ МС ──────────────────────────────────

export interface InventoryStore {
  id: string
  name: string
  href: string
}

export interface SnapshotStatus {
  running: boolean
  size: number
  at: string | null
  result: any
}

export interface ReconcileRow {
  gtin: string | null
  product_name: string | null
  folder_id: string | null
  folder_name: string
  qty_cz: number
  qty_upd: number
  qty_ms: number
  diff: number
  to_search: number
  not_in_ms?: boolean
}

export interface ReconcileBrand {
  folder_id: string | null
  folder_name: string
  positions: number
  qty_cz: number
  qty_upd: number
  qty_ms: number
  diff: number
  to_search: number
}

export interface ReconcileResult {
  has_ms_snapshot: boolean
  ms_size: number
  cz_size: number
  search_total: number
  brands: ReconcileBrand[]
  rows: ReconcileRow[]
  totals: { positions: number; qty_cz: number; qty_upd: number; qty_ms: number; diff: number; to_search: number }
}

export type ReconcileDiff = 'all' | 'to_search' | 'cz_gt_ms' | 'ms_gt_cz' | 'mismatch'

export interface StoresResponse {
  stores: InventoryStore[]
  selected: string[]
  available: boolean
}

export const inventoryApi = {
  stores: () => api.get<StoresResponse>('/inventory/stores'),
  saveStores: (store_ids: string[]) =>
    api.post<StoresResponse>('/inventory/stores', { store_ids }),
  czRefresh: () => api.post<{ status: string }>('/inventory/cz-stock/refresh'),
  czStatus: () => api.get<SnapshotStatus>('/inventory/cz-stock/status'),
  msRefresh: () => api.post<{ status: string }>('/inventory/ms-stock/refresh'),
  msStatus: () => api.get<SnapshotStatus>('/inventory/ms-stock/status'),
  reconcile: (brand?: string, diff: ReconcileDiff = 'all') =>
    api.get<ReconcileResult>('/inventory/reconcile', { params: { brand: brand || undefined, diff } }),
  reconcileXlsx: (brand?: string, diff: ReconcileDiff = 'all') =>
    api.get('/inventory/reconcile.xlsx', { params: { brand: brand || undefined, diff }, responseType: 'blob' }),
}

export const documentsApi = {
  listMs: (kind: DocumentKind, search?: string) =>
    api.get<MsDocument[]>(`/documents/moysklad/${kind}`, {
      params: search ? { search } : {},
    }),
  list: (kind?: DocumentKind) =>
    api.get<Document[]>('/documents/', { params: kind ? { kind } : {} }),
  create: (name: string, kind: DocumentKind, moysklad_id?: string) =>
    api.post<Document>('/documents/', { name, kind, moysklad_id }),
  get: (id: string) => api.get<Document>(`/documents/${id}`),
  refreshPlan: (id: string) => api.post<Document>(`/documents/${id}/refresh-plan`),
  verify: (id: string) =>
    api.post<{ status: string; document_id: string; count: number }>(
      `/documents/${id}/verify`,
    ),
  process: (id: string) => api.post(`/documents/${id}/process`),
  exportXlsx: (id: string) =>
    api.get<Blob>(`/documents/${id}/export.xlsx`, { responseType: 'blob' }),
}

export function isSscc(code: string): boolean {
  return code.length === 20 && code.startsWith('00') && /^\d+$/.test(code)
}

export type ScanStatus =
  | 'pending'
  | 'scanned'
  | 'valid'
  | 'invalid'
  | 'duplicate'
  | 'overflow'
  | 'unknown_product'
  | 'used_in_other_doc'

export interface Scan {
  id: string
  document_id: string
  code: string
  gtin: string | null
  status: ScanStatus
  product_name: string | null
  moysklad_product_id?: string | null
  error_message: string | null
  scanned_at: string
  /** Короб SSCC, сохранённый целиком (transportpack). */
  is_box?: boolean
  /** Скан обычного штрихкода немаркированного товара (не КМ): box_quantity — кол-во. */
  is_barcode?: boolean
  /** Число SGTIN внутри короба/блока (из ЧЗ) либо кол-во для штрихкодового скана. */
  box_quantity?: number | null
  /** Владелец и производитель КМ из ЧЗ (cises/info). */
  owner_name?: string | null
  producer_name?: string | null
  /** ИНН владельца марки (ЧЗ) — сверка с владельцем подписи (cz_inn) в отгрузке. */
  owner_inn?: string | null
  /** Марка выведена из оборота / заблокирована (ЧЗ markWithdraw) + причина. */
  withdrawn?: boolean
  withdraw_reason?: string | null
  /** Состав агрегата (блок/короб): КМ вложенных пачек из ЧЗ. */
  child_codes?: string[] | null
  /** Повторный скан кода, уже присутствующего в этом документе (строка не добавляется). */
  duplicate?: boolean
}

/** Документ, в котором найдена искомая марка (ответ /scans/search). */
export interface CodeSearchHit {
  document_id: string
  document_name: string
  document_kind: string
  scan_id: string
  code: string
  status: ScanStatus
  product_name: string | null
  scanned_at: string
}

export const scansApi = {
  create: (document_id: string, code: string, moysklad_product_id?: string | null) =>
    api.post<Scan>('/scans/', {
      document_id,
      code,
      ...(moysklad_product_id ? { moysklad_product_id } : {}),
    }),
  /** Принять SSCC-короб. unpack=true — раскрыть на штучные КМ; false — целиком (transportpack). */
  box: (document_id: string, sscc: string, unpack: boolean) =>
    api.post<Scan[]>('/scans/box', { document_id, sscc, unpack }),
  /** Массовая загрузка списка марок (отгрузка): проходят обычный конвейер проверки. */
  bulk: (document_id: string, codes: string[], unpack_boxes = false) =>
    api.post<Scan[]>('/scans/bulk', { document_id, codes, unpack_boxes }),
  patchProduct: (scan_id: string, moysklad_product_id: string | null) =>
    api.patch<Scan>(`/scans/item/${scan_id}`, { moysklad_product_id }),
  list: (document_id: string) => api.get<Scan[]>(`/scans/${document_id}`),
  delete: (scan_id: string) => api.delete(`/scans/${scan_id}`),
  /** Удалить пачку сканов по id (напр. позицию не из плана целиком). */
  deleteBulk: (document_id: string, scan_ids: string[]) =>
    api.post<{ deleted: number }>('/scans/delete-bulk', { document_id, scan_ids }),
  /** Удалить все марки документа из БД. */
  clearDocument: (document_id: string) => api.delete(`/scans/by-document/${document_id}`),
  /** Найти документы пользователя, где уже есть указанный код маркировки. */
  searchByCode: (code: string) =>
    api.get<CodeSearchHit[]>('/scans/search', { params: { code } }),
}

export interface ProductSearchItem {
  id: string
  name: string
  article: string
  code: string
  barcodes: string[]
}

export interface MatchSuggestion {
  gtin: string
  gtin_key: string
  name: string | null
  count: number
  confidence: 'high' | 'low' | 'none'
  best: ProductSearchItem | null
  suggestions: ProductSearchItem[]
}

export interface BulkLinkItem {
  gtin: string
  moysklad_product_id: string
  product_name?: string | null
}

export const productsApi = {
  search: (q: string) =>
    api.get<ProductSearchItem[]>('/products/search', { params: { q } }),
  linkGtin: (
    document_id: string,
    gtin: string,
    moysklad_product_id: string,
    product_name?: string | null,
  ) =>
    api.post<{ updated_count: number }>('/products/link-gtin', {
      document_id,
      gtin,
      moysklad_product_id,
      product_name: product_name ?? null,
    }),
  matchSuggestions: (document_id: string) =>
    api.get<MatchSuggestion[]>('/products/match-suggestions', {
      params: { document_id },
    }),
  linkGtinBulk: (document_id: string, links: BulkLinkItem[]) =>
    api.post<{ updated_total: number; results: { gtin: string; updated: number }[] }>(
      '/products/link-gtin-bulk',
      { document_id, links },
    ),
}

// ---------- Приёмка по УПД (XML 5.03) ----------

export interface ProductGroup {
  code: string
  label: string
}

export interface AcceptanceDoc {
  id: string
  name: string
  kind: string
  status: 'draft' | 'processing' | 'accepted'
  product_group: string | null
  /** Привязанное поступление МС (если выбрано) — куда пишутся КМ. */
  moysklad_id: string | null
  scan_count: number
  /** Кол-во позиций поступления МС, подтянутых в план. */
  plan_count: number
  /** Причина неуспешной отправки в МС (напр. истёк токен ЧЗ), если была. */
  error_message: string | null
}

export interface ImportPositionResult {
  name: string
  gtin: string | null
  article: string | null
  quantity: number | null
  codes_count: number
  packages_count: number
  product_id: string | null
  product_name: string | null
  matched: boolean
  /** Номер строки позиции в таблице УПД (НомСтр). */
  line_number: number | null
  /** Цена за единицу С НДС (руб.) из УПД. */
  price: number | null
  /** Ставка НДС (НалСт, %) из УПД. */
  vat: number | null
}

export interface ImportUpdResult {
  document_id: string
  positions: ImportPositionResult[]
  created_scans: number
  skipped_duplicates: number
  unmatched_gtins: string[]
  /** Сумма по УПД с НДС (СтТовУчНалВсего из <ВсегоОпл>). */
  total_amount: number | null
  /** Итоговая сумма НДС (СумНалВсего/СумНал из <ВсегоОпл>). */
  total_vat: number | null
}

export const acceptanceApi = {
  productGroups: () => api.get<ProductGroup[]>('/acceptance/product-groups'),
  createDoc: (name: string, product_group: string, moysklad_id?: string) =>
    api.post<AcceptanceDoc>('/acceptance/documents', {
      name,
      product_group,
      ...(moysklad_id ? { moysklad_id } : {}),
    }),
  getDoc: (id: string) => api.get<AcceptanceDoc>(`/acceptance/documents/${id}`),
  importUpd: (documentId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.post<ImportUpdResult>(
      `/acceptance/documents/${documentId}/import-upd`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
  },
  /** Ручная загрузка списка марок в приёмку (альтернатива УПД). */
  importMarks: (documentId: string, codes: string[]) =>
    api.post<ImportUpdResult>(
      `/acceptance/documents/${documentId}/import-marks`,
      { codes },
    ),
}

export interface Integration {
  has_moysklad: boolean
  moysklad_account_name: string | null
  has_cz: boolean
  cz_token_valid_until: string | null
  cz_cert_subject: string | null
  cz_auth_method: string
  cz_box_mode_enabled: boolean
  cz_inn: string | null
  cz_product_groups: string[]
}

export interface ProductGroup {
  code: string
  label: string
}

export const integrationsApi = {
  get: () => api.get<Integration>('/integrations/'),
  update: (data: {
    moysklad_token?: string
    cz_box_mode_enabled?: boolean
    cz_inn?: string
    cz_product_groups?: string[]
  }) => api.put<Integration>('/integrations/', data),
  productGroups: () =>
    api.get<ProductGroup[]>('/integrations/cz/product-groups'),
}

export interface CzChallenge {
  uuid: string
  data: string
}

export interface CzLoginResult {
  cz_token_valid_until: string
  cz_cert_subject: string | null
}

export interface WriteoffPart {
  pg: string
  product_document_b64: string
}

export interface UnresolvedCode {
  cis: string
  reason: string
}

export interface WriteoffPrepareResult {
  writeoff_token: string
  parts: WriteoffPart[]
  unresolved: UnresolvedCode[]
}

export const WRITEOFF_REASONS: { value: string; label: string }[] = [
  { value: 'spoilage', label: 'Порча, утилизация' },
  { value: 'own_use', label: 'Собственные нужды предприятия' },
  { value: 'demo', label: 'Демонстрационные образцы' },
  { value: 'general_business', label: 'Списание на общехозяйственные расходы' },
  { value: 'production', label: 'Списание на производственные расходы' },
  { value: 'non_commercial', label: 'Списание на общехоз. некоммерческую деятельность' },
]

export const czApi = {
  challenge: () => api.post<CzChallenge>('/integrations/cz/challenge'),
  login: (body: {
    uuid: string
    signed_data: string
    cert_thumbprint?: string
    cert_subject?: string
  }) => api.post<CzLoginResult>('/integrations/cz/login', body),
  logout: () => api.delete<Integration>('/integrations/cz'),
  writeoffPrepare: (body: {
    document_id: string
    reason: string
    basis_number?: string
    basis_date?: string
  }) => api.post<WriteoffPrepareResult>('/integrations/cz/writeoff/prepare', body),
  writeoffSubmit: (body: {
    writeoff_token: string
    signatures: { pg: string; signature: string }[]
  }) => api.post<{ doc_ids: string[] }>('/integrations/cz/writeoff/submit', body),
  check: (codes: string[]) =>
    api.post<CzCheckResult>('/integrations/cz/check', { codes }),
}

export interface CzCheckDocRef {
  document_id: string
  name: string
  kind: DocumentKind
  scan_status: string
}

export interface CzCheckItem {
  code: string
  found: boolean
  product_name: string | null
  gtin: string | null
  owner_name: string | null
  owner_inn: string | null
  producer_name: string | null
  product_group: string | null
  status: string | null
  package_type: string | null
  child_count: number
  error: string | null
  documents: CzCheckDocRef[]
}

export interface CzCheckResult {
  items: CzCheckItem[]
}

// ---------- Техподдержка (тикеты в AXIMA ERP) ----------

export interface SupportCategory {
  value: string
  label: string
}

export interface SupportTicketResult {
  ok: boolean
  ref: string | null
}

export const supportApi = {
  categories: () => api.get<SupportCategory[]>('/support/categories'),
  createTicket: (body: {
    subject: string
    message: string
    category: string
    page?: string
  }) => api.post<SupportTicketResult>('/support/ticket', body),
}
