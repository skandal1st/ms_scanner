import { useEffect, useState, type CSSProperties } from 'react'
import { SettingsPage } from './Settings'
import { Icon } from '../components/Icon'
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
          <img src="/logo.svg" alt="Скандата" style={styles.brandLogo} />
          {employee_name && (
            <div style={styles.muted}>
              {employee_name}
              {account_name ? ` · ${account_name}` : ''}
            </div>
          )}
        </div>
        <div style={styles.ctaGroup}>
          <button type="button" style={styles.cta} onClick={openShipment}>
            <Icon name="shipment" size={17} /> Начать отгрузку
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
    fontFamily: 'var(--ms-font)',
    background: 'var(--ms-bg-alt)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    padding: '16px 20px',
    background: 'var(--ms-bg)',
    borderBottom: '1px solid var(--ms-border-light)',
    position: 'sticky',
    top: 0,
    zIndex: 10,
  },
  brand: {
    fontSize: 17,
    fontWeight: 700,
    color: 'var(--ms-text)',
    letterSpacing: '-0.01em',
  },
  brandLogo: {
    height: 30,
    width: 'auto',
    display: 'block',
  },
  muted: {
    fontSize: 12,
    color: 'var(--ms-text-muted)',
    marginTop: 2,
  },
  ctaGroup: {
    display: 'flex',
    gap: 8,
    flexWrap: 'wrap',
    justifyContent: 'flex-end',
  },
  cta: {
    background: 'var(--brand)',
    color: '#fff',
    border: 'none',
    borderRadius: 'var(--r-md)',
    padding: '11px 20px',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    boxShadow: 'var(--shadow-1)',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
  },
  main: {
    flex: 1,
    padding: '16px 20px',
  },
  footer: {
    padding: '10px 20px 14px',
    fontSize: 11,
    color: 'var(--ms-text-subtle)',
    textAlign: 'center',
  },
  centered: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    fontFamily: 'var(--ms-font)',
  },
  errorBox: {
    color: 'var(--st-err-fg)',
    background: 'var(--st-err-bg)',
    border: '1px solid var(--st-err-bd)',
    borderRadius: 'var(--r-md)',
    padding: 16,
    maxWidth: 480,
    fontSize: 13,
  },
}
