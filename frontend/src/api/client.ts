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

export type DocumentKind = 'demand' | 'loss' | 'salesreturn'

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

export type ScanStatus = 'pending' | 'valid' | 'invalid' | 'duplicate' | 'overflow'

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
}

export const scansApi = {
  create: (document_id: string, code: string, moysklad_product_id?: string | null) =>
    api.post<Scan>('/scans/', {
      document_id,
      code,
      ...(moysklad_product_id ? { moysklad_product_id } : {}),
    }),
  patchProduct: (scan_id: string, moysklad_product_id: string | null) =>
    api.patch<Scan>(`/scans/item/${scan_id}`, { moysklad_product_id }),
  list: (document_id: string) => api.get<Scan[]>(`/scans/${document_id}`),
  delete: (scan_id: string) => api.delete(`/scans/${scan_id}`),
}

export interface Integration {
  has_moysklad: boolean
  moysklad_account_name: string | null
}

export const integrationsApi = {
  get: () => api.get<Integration>('/integrations/'),
  update: (data: { moysklad_token?: string }) =>
    api.put<Integration>('/integrations/', data),
}
