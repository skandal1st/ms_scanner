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
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default api

// --- Auth ---
export const authApi = {
  login: (email: string, password: string) =>
    api.post<{ access_token: string; refresh_token: string }>('/auth/login', { email, password }),
  register: (email: string, password: string) =>
    api.post<{ access_token: string; refresh_token: string }>('/auth/register', { email, password }),
}

// --- Documents ---
// move исключён: XSD-схема дескриптора не разрешает update для перемещений
// через scope=custom, а вся логика отгрузки = запись trackingCodes через PUT.
export type DocumentKind = 'supply' | 'demand' | 'loss' | 'salesreturn'

export interface MsDocument {
  id: string
  name: string
  moment: string | null
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
  listMs: (kind: DocumentKind) =>
    api.get<MsDocument[]>(`/documents/moysklad/${kind}`),
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

// --- Scans ---
export type ScanStatus = 'pending' | 'valid' | 'invalid' | 'duplicate' | 'overflow'

export interface Scan {
  id: string
  document_id: string
  code: string
  gtin: string | null
  status: ScanStatus
  product_name: string | null
  error_message: string | null
  scanned_at: string
}

export const scansApi = {
  create: (document_id: string, code: string) =>
    api.post<Scan>('/scans/', { document_id, code }),
  createBox: (document_id: string, sscc: string) =>
    api.post<Scan[]>('/scans/box', { document_id, sscc }),
  list: (document_id: string) => api.get<Scan[]>(`/scans/${document_id}`),
  delete: (scan_id: string) => api.delete(`/scans/${scan_id}`),
}

// --- Integrations ---
export interface Integration {
  has_moysklad: boolean
  moysklad_account_name: string | null
  has_cz: boolean
  cz_token_valid_until: string | null
  cz_cert_subject: string | null
  cz_auth_method: 'mock' | 'cprob_plugin'
}

export const integrationsApi = {
  get: () => api.get<Integration>('/integrations/'),
  update: (data: { moysklad_token?: string; cz_token?: string }) =>
    api.put<Integration>('/integrations/', data),
}

// --- ЧЗ авторизация через УКЭП (CryptoPro Browser Plugin) ---
export interface CzChallenge {
  uuid: string
  data: string
}

export interface CzLoginResult {
  cz_token_valid_until: string
  cz_cert_subject: string | null
}

export const czAuthApi = {
  challenge: () => api.post<CzChallenge>('/integrations/cz/challenge'),
  login: (body: {
    uuid: string
    signed_data: string
    cert_thumbprint?: string
    cert_subject?: string
  }) => api.post<CzLoginResult>('/integrations/cz/login', body),
  logout: () => api.delete<Integration>('/integrations/cz'),
}
