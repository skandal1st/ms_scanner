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

export type DocumentKind = 'demand'

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
}

export interface Document {
  id: string
  moysklad_id: string | null
  name: string
  kind: DocumentKind
  status: 'draft' | 'processing' | 'accepted'
  scan_count: number
  plan: PlanItem[]
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
  /** Число SGTIN внутри короба/блока (из ЧЗ). */
  box_quantity?: number | null
  /** Владелец и производитель КМ из ЧЗ (cises/info). */
  owner_name?: string | null
  producer_name?: string | null
  /** Состав агрегата (блок/короб): КМ вложенных пачек из ЧЗ. */
  child_codes?: string[] | null
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
  patchProduct: (scan_id: string, moysklad_product_id: string | null) =>
    api.patch<Scan>(`/scans/item/${scan_id}`, { moysklad_product_id }),
  list: (document_id: string) => api.get<Scan[]>(`/scans/${document_id}`),
  delete: (scan_id: string) => api.delete(`/scans/${scan_id}`),
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

export interface Integration {
  has_moysklad: boolean
  moysklad_account_name: string | null
  has_cz: boolean
  cz_token_valid_until: string | null
  cz_cert_subject: string | null
  cz_auth_method: string
  cz_box_mode_enabled: boolean
}

export const integrationsApi = {
  get: () => api.get<Integration>('/integrations/'),
  update: (data: { moysklad_token?: string; cz_box_mode_enabled?: boolean }) =>
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

export const czApi = {
  challenge: () => api.post<CzChallenge>('/integrations/cz/challenge'),
  login: (body: {
    uuid: string
    signed_data: string
    cert_thumbprint?: string
    cert_subject?: string
  }) => api.post<CzLoginResult>('/integrations/cz/login', body),
  logout: () => api.delete<Integration>('/integrations/cz'),
}
