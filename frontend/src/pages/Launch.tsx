import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

export function LaunchPage() {
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const launchToken = params.get('t')
    const mode = params.get('mode')
    const target = mode === 'shipment' ? '/shipment' : '/'

    // Если у нас уже есть JWT (страница перезагрузилась, либо повторный заход
    // с тем же URL после успешного обмена) — не дёргаем /auth/launch повторно.
    // launch_token одноразовый (GETDEL), повторный обмен вернёт 401 «Ссылка
    // устарела», и пользователь увидит ошибку, хотя сессия живая.
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
        navigate(target, { replace: true })
      })
      .catch((err) => {
        // Если в процессе мы успели получить JWT (двойной useEffect в Strict
        // Mode и т.п.) — лучше пустить пользователя, чем пугать ошибкой.
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
        fontFamily: 'system-ui, -apple-system, sans-serif',
        padding: 24,
      }}
    >
      {error ? (
        <div
          style={{
            color: '#b91c1c',
            background: '#fef2f2',
            border: '1px solid #fecaca',
            borderRadius: 8,
            padding: 16,
            maxWidth: 420,
          }}
        >
          <div style={{ fontWeight: 500, marginBottom: 6 }}>Не удалось открыть приложение</div>
          <div style={{ fontSize: 13 }}>{error}</div>
          <div style={{ fontSize: 12, color: '#6b7280', marginTop: 12 }}>
            Вернитесь в МойСклад и снова откройте приложение из карточки решения.
          </div>
        </div>
      ) : (
        <div style={{ color: '#6b7280' }}>Открываем приёмку…</div>
      )}
    </div>
  )
}
