import { useEffect, useState, type CSSProperties } from 'react'

interface LaunchPayload {
  launch_token: string
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
        setState({ kind: 'ready', payload: data as LaunchPayload })
      })
      .catch((err) => {
        const message = typeof err?.message === 'string' ? err.message : 'Ошибка авторизации через МойСклад'
        setState({ kind: 'error', message })
      })
  }, [])

  const handleOpen = () => {
    if (state.kind !== 'ready') return
    // namespace вкладки оставляем без 'noopener', чтобы потом сработал window.close()
    // после «Принять товары». Origin лаунчера и новой вкладки совпадает (skandata.ru),
    // tabnabbing-риска нет.
    window.open(`/launch?t=${encodeURIComponent(state.payload.launch_token)}`, '_blank')
  }

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <div style={styles.brand}>МС-Сканер</div>
        <div style={styles.subtitle}>Приёмка маркированных товаров</div>

        {state.kind === 'loading' && (
          <div style={styles.muted}>Подключаемся к МойСклад…</div>
        )}

        {state.kind === 'error' && (
          <div style={styles.error}>{state.message}</div>
        )}

        {state.kind === 'ready' && (
          <>
            <div style={styles.greeting}>
              {state.payload.employee_name ? `Привет, ${state.payload.employee_name}!` : 'Готово!'}
            </div>
            {state.payload.account_name && (
              <div style={styles.muted}>Аккаунт: {state.payload.account_name}</div>
            )}
            <button type="button" style={styles.button} onClick={handleOpen}>
              📦 Открыть приёмку маркировки
            </button>
            <div style={styles.hint}>откроется в новой вкладке</div>
          </>
        )}
      </div>
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  page: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontFamily: 'system-ui, -apple-system, sans-serif',
    background: '#f5f6f8',
    padding: 16,
  },
  card: {
    background: '#fff',
    borderRadius: 12,
    padding: '32px 28px',
    width: '100%',
    maxWidth: 420,
    boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
    textAlign: 'center',
  },
  brand: {
    fontSize: 22,
    fontWeight: 600,
    color: '#1f2937',
  },
  subtitle: {
    fontSize: 13,
    color: '#6b7280',
    marginTop: 4,
    marginBottom: 24,
  },
  greeting: {
    fontSize: 17,
    fontWeight: 500,
    color: '#1f2937',
    marginBottom: 6,
  },
  muted: {
    fontSize: 13,
    color: '#6b7280',
    marginBottom: 16,
  },
  hint: {
    fontSize: 12,
    color: '#9ca3af',
    marginTop: 10,
  },
  button: {
    background: '#16a34a',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    padding: '12px 20px',
    fontSize: 15,
    fontWeight: 500,
    cursor: 'pointer',
    width: '100%',
    marginTop: 12,
  },
  error: {
    color: '#b91c1c',
    background: '#fef2f2',
    border: '1px solid #fecaca',
    borderRadius: 8,
    padding: 12,
    fontSize: 13,
    textAlign: 'left',
  },
}
