import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from 'axios'

export interface TsdTokens {
  access_token: string
  refresh_token: string
}

export function saveTsdTokens(tokens: TsdTokens) {
  localStorage.setItem('tsd_refresh_token', tokens.refresh_token)
  localStorage.setItem('tsd_access_token', tokens.access_token)
}

export function installTsdAuth(client: AxiosInstance) {
  let refreshing: Promise<void> | null = null
  const clearBinding = () => {
    localStorage.removeItem('tsd_access_token')
    localStorage.removeItem('tsd_refresh_token')
    localStorage.removeItem('tsd_document_id')
    window.location.href = '/tsd'
  }
  const refresh = () => {
    if (!refreshing) {
      const credential = localStorage.getItem('tsd_refresh_token') || localStorage.getItem('tsd_access_token')
      refreshing = axios.post<TsdTokens>('/api/tsd/auth/refresh', { refresh_token: credential }, { timeout: 20_000 })
        .then(({ data }) => { saveTsdTokens(data) })
        .catch(error => {
          // Network failures and subscription restrictions must preserve the binding.
          if (axios.isAxiosError(error) && error.response?.status === 401) clearBinding()
          throw error
        })
        .finally(() => { refreshing = null })
    }
    return refreshing
  }

  client.interceptors.request.use(async config => {
    if (!navigator.onLine) throw new Error('Нет сети. Подключитесь к Wi-Fi и повторите операцию.')
    // Upgrade existing, still-valid shift tokens without requiring another QR.
    const access = localStorage.getItem('tsd_access_token')
    const binding = localStorage.getItem('tsd_refresh_token')
    if (Boolean(access) !== Boolean(binding)) await refresh()
    const token = localStorage.getItem('tsd_access_token')
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })

  client.interceptors.response.use(response => response, async error => {
    const config = error.config as (InternalAxiosRequestConfig & { tsdRetried?: boolean }) | undefined
    if (error.response?.status !== 401 || !config) throw error
    if (config.tsdRetried) {
      clearBinding()
      throw error
    }
    if (!localStorage.getItem('tsd_access_token') && !localStorage.getItem('tsd_refresh_token')) throw error
    config.tsdRetried = true
    const token = localStorage.getItem('tsd_access_token')
    // Another request may already have renewed the token while this one was in flight.
    if (!token || config.headers.Authorization === `Bearer ${token}`) await refresh()
    return client.request(config)
  })
}
