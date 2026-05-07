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
export interface MsSupply {
  id: string
  name: string
  moment: string | null
}

export interface Document {
  id: string
  moysklad_id: string | null
  name: string
  status: 'draft' | 'processing' | 'accepted'
  scan_count: number
  created_at: string
}

export const documentsApi = {
  listMsSupplies: () => api.get<MsSupply[]>('/documents/moysklad-supplies'),
  list: () => api.get<Document[]>('/documents/'),
  create: (name: string, moysklad_id?: string) =>
    api.post<Document>('/documents/', { name, moysklad_id }),
  get: (id: string) => api.get<Document>(`/documents/${id}`),
  accept: (id: string) => api.post(`/documents/${id}/accept`),
}

// --- Scans ---
export interface Scan {
  id: string
  document_id: string
  code: string
  gtin: string | null
  status: 'pending' | 'valid' | 'invalid' | 'duplicate'
  product_name: string | null
  error_message: string | null
  scanned_at: string
}

export const scansApi = {
  create: (document_id: string, code: string) =>
    api.post<Scan>('/scans/', { document_id, code }),
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
