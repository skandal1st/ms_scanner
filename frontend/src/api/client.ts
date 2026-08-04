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

api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user_id')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default api

export const authApi = {
  login: (email: string, password: string) =>
    api.post<{ access_token: string; refresh_token: string }>('/auth/login', { email, password }),
  register: (email: string, password: string) =>
    api.post<{ access_token: string; refresh_token: string }>('/auth/register', { email, password }),
}

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
  created_at: string
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
  process: (id: string) => api.post(`/documents/${id}/process`),
}

export function isSscc(code: string): boolean {
  return code.length === 20 && code.startsWith('00') && /^\d+$/.test(code)
}

export type ScanStatus =
  | 'pending'
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
}

export const integrationsApi = {
  get: () => api.get<Integration>('/integrations/'),
  update: (data: { moysklad_token?: string; cz_box_mode_enabled?: boolean; cz_inn?: string }) =>
    api.put<Integration>('/integrations/', data),
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

export interface WriteoffPrepareResult {
  writeoff_token: string
  parts: WriteoffPart[]
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
}
