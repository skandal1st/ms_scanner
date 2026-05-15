import { useEffect, useState, type CSSProperties } from 'react'
import { SettingsPage } from './Settings'
import { persistUserIdFromAccessToken } from '../lib/jwt'

interface LaunchPayload {
  launch_token: string
  access_token: string
  refresh_token: string
  employee_name: string | null
  account_name: string | null
}

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; payload: LaunchPayload }
  | { kind: 'error'; message: string }

export function MsIframePage() {
  const [state, setState] = useState<LoadState>({ kind: 'loading' })

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const contextKey = params.get('contextKey')
    if (!contextKey) {
      setState({
        kind: 'error',
        message: 'contextKey не передан. Откройте приложение через карточку решения в МойСклад.',
      })
      return
    }

    fetch('/api/auth/ms-launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contextKey }),
    })
      .then(async (resp) => {
        const data = await resp.json().catch(() => ({}))
        if (!resp.ok) {
          throw new Error(data?.detail || `HTTP ${resp.status}`)
        }
        const payload = data as LaunchPayload
        localStorage.setItem('access_token', payload.access_token)
        localStorage.setItem('refresh_token', payload.refresh_token)
        persistUserIdFromAccessToken(payload.access_token)
        setState({ kind: 'ready', payload })
      })
      .catch((err) => {
        const message = typeof err?.message === 'string'
          ? err.message
          : 'Ошибка авторизации через МойСклад'
        setState({ kind: 'error', message })
      })
  }, [])

  const openShipment = () => {
    if (state.kind !== 'ready') return
    const t = encodeURIComponent(state.payload.launch_token)
    window.open(`/launch?t=${t}&mode=shipment`, '_blank')
  }

  if (state.kind === 'loading') {
    return (
      <div style={styles.centered}>
        <div style={styles.muted}>Подключаемся к МойСклад...</div>
      </div>
    )
  }

  if (state.kind === 'error') {
    return (
      <div style={styles.centered}>
        <div style={styles.errorBox}>{state.message}</div>
      </div>
    )
  }

  const { employee_name, account_name } = state.payload

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <div>
          <div style={styles.brand}>МС-Сканер</div>
          {employee_name && (
            <div style={styles.muted}>
              {employee_name}
              {account_name ? ` · ${account_name}` : ''}
            </div>
          )}
        </div>
        <div style={styles.ctaGroup}>
          <button type="button" style={styles.cta} onClick={openShipment}>
            Начать отгрузку
          </button>
        </div>
      </header>

      <main style={styles.main}>
        <SettingsPage embedded />
      </main>

      <footer style={styles.footer}>
        Сборка отгрузки откроется в новой вкладке, чтобы USB-сканер оставался в фокусе.
      </footer>
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  page: {
    minHeight: '100vh',
    display: 'flex',
    flexDirection: 'column',
    fontFamily: 'system-ui, -apple-system, sans-serif',
    background: '#f5f6f8',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    padding: '14px 20px',
    background: '#fff',
    borderBottom: '1px solid #e5e7eb',
    position: 'sticky',
    top: 0,
    zIndex: 10,
  },
  brand: {
    fontSize: 16,
    fontWeight: 600,
    color: '#1f2937',
  },
  muted: {
    fontSize: 12,
    color: '#6b7280',
    marginTop: 2,
  },
  ctaGroup: {
    display: 'flex',
    gap: 8,
    flexWrap: 'wrap',
    justifyContent: 'flex-end',
  },
  cta: {
    background: '#16a34a',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    padding: '10px 18px',
    fontSize: 14,
    fontWeight: 500,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
  main: {
    flex: 1,
    padding: '16px 20px',
  },
  footer: {
    padding: '10px 20px 14px',
    fontSize: 11,
    color: '#9ca3af',
    textAlign: 'center',
  },
  centered: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    fontFamily: 'system-ui, -apple-system, sans-serif',
  },
  errorBox: {
    color: '#b91c1c',
    background: '#fef2f2',
    border: '1px solid #fecaca',
    borderRadius: 8,
    padding: 16,
    maxWidth: 480,
    fontSize: 13,
  },
}
