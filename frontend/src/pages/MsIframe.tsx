import { useEffect, useRef, useState, type CSSProperties } from 'react'
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
  const pageRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const page = pageRef.current
    if (!page || window.parent === window) return

    let previousHeight = 0
    let frame = 0
    const reportHeight = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        // Measure content, not the viewport: the host resizes the iframe itself.
        const height = Math.ceil(page.getBoundingClientRect().height)
        if (height <= 0 || height === previousHeight) return
        previousHeight = height
        // The official iframe expand protocol sends only { height }.
        window.parent.postMessage({ height }, '*')
      })
    }
    const observer = new ResizeObserver(reportHeight)
    observer.observe(page)
    reportHeight()
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
    }
  }, [state.kind])

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

  if (state.kind === 'loading') {
    return (
      <div ref={pageRef} style={styles.centered}>
        <div style={styles.muted}>Подключаемся к МойСклад...</div>
      </div>
    )
  }

  if (state.kind === 'error') {
    return (
      <div ref={pageRef} style={styles.centered}>
        <div style={styles.errorBox}>{state.message}</div>
      </div>
    )
  }

  const { employee_name, account_name } = state.payload

  return (
    <div ref={pageRef} style={styles.page}>
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
      </header>

      <main style={styles.main}>
        <SettingsPage embedded />
      </main>

    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  page: {
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
  },
  brandLogo: {
    height: 40,
    width: 'auto',
    display: 'block',
  },
  muted: {
    fontSize: 12,
    color: 'var(--ms-text-muted)',
    marginTop: 2,
  },
  main: {
    padding: '16px 20px',
  },
  centered: {
    minHeight: 240,
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
