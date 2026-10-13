import { useEffect } from 'react'

// Public sign-in is hidden during MoySklad moderation. Keep /login as the
// recovery route for expired sessions and client-side authentication guards.
export function LoginPage() {
  useEffect(() => {
    window.location.replace('https://online.moysklad.ru/')
  }, [])

  return (
    <div className="login-page">
      <p role="status">
        Переходим в МойСклад…{' '}
        <a href="https://online.moysklad.ru/">Открыть МойСклад</a>
      </p>
    </div>
  )
}
