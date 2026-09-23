import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { persistUserIdFromAccessToken } from '../lib/jwt'

export function LaunchPage() {
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const launchToken = params.get('t')
    // mode задаёт, какой раздел открыть в новой вкладке: приёмка/отгрузка/списание.
    const mode = params.get('mode')
    // doc — наш внутренний id документа (из кнопки МС): страница сразу его выберет.
    const docId = params.get('doc')
    // Внешняя вкладка = киоск-окно сканирования без навигации по разделам.
    const base =
      mode === 'acceptance'
        ? '/scan/acceptance'
        : mode === 'writeoff'
          ? '/scan/writeoff'
          : '/scan/shipment'
    const target = docId ? `${base}?doc=${encodeURIComponent(docId)}` : base

    if (localStorage.getItem('access_token')) {
      navigate(target, { replace: true })
      return
    }

    if (!launchToken) {
      setError('Ссылка повреждена: нет токена.')
      return
    }

    fetch('/api/auth/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ launch_token: launchToken }),
    })
      .then(async (resp) => {
        const data = await resp.json().catch(() => ({}))
        if (!resp.ok) {
          throw new Error(data?.detail || `HTTP ${resp.status}`)
        }
        localStorage.setItem('access_token', data.access_token)
        localStorage.setItem('refresh_token', data.refresh_token)
        persistUserIdFromAccessToken(data.access_token)
        navigate(target, { replace: true })
      })
      .catch((err) => {
        if (localStorage.getItem('access_token')) {
          navigate(target, { replace: true })
          return
        }
        const message = typeof err?.message === 'string' ? err.message : 'Не удалось войти'
        setError(message)
      })
  }, [navigate])

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'var(--ms-font)',
        background: 'var(--ms-bg-alt)',
        padding: 24,
      }}
    >
      {error ? (
        <div
          style={{
            color: 'var(--st-err-fg)',
            background: 'var(--st-err-bg)',
            border: '1px solid var(--st-err-bd)',
            borderRadius: 'var(--r-md)',
            padding: 16,
            maxWidth: 420,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Не удалось открыть приложение</div>
          <div style={{ fontSize: 13 }}>{error}</div>
          <div style={{ fontSize: 12, color: 'var(--ms-text-muted)', marginTop: 12 }}>
            Вернитесь в МойСклад и снова откройте приложение из карточки решения.
          </div>
        </div>
      ) : (
        <div style={{ color: 'var(--ms-text-muted)' }}>
          {(() => {
            const m = new URLSearchParams(window.location.search).get('mode')
            if (m === 'acceptance') return 'Открываем приёмку...'
            if (m === 'writeoff') return 'Открываем списание...'
            return 'Открываем отгрузку...'
          })()}
        </div>
      )}
    </div>
  )
}
