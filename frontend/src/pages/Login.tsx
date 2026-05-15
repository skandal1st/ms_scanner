import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authApi } from '../api/client'
import { persistUserIdFromAccessToken } from '../lib/jwt'

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const { data } = mode === 'login'
        ? await authApi.login(email, password)
        : await authApi.register(email, password)

      localStorage.setItem('access_token', data.access_token)
      if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token)
      persistUserIdFromAccessToken(data.access_token)
      navigate('/')
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Ошибка. Попробуйте снова.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-card__title">МС-Сканер</h1>
        <p className="login-card__sub">Сборка отгрузок маркированной продукции</p>

        <ul className="tabs__buttons" role="tablist">
          <li
            className={`tabs__button ${mode === 'login' ? 'b-active' : ''}`}
            role="tab"
            aria-selected={mode === 'login'}
            onClick={() => setMode('login')}
          >
            Войти
          </li>
          <li
            className={`tabs__button ${mode === 'register' ? 'b-active' : ''}`}
            role="tab"
            aria-selected={mode === 'register'}
            onClick={() => setMode('register')}
          >
            Регистрация
          </li>
        </ul>

        <form onSubmit={handleSubmit} className="login-form">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email"
            required
            className="ui-input ui-input--block"
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Пароль"
            required
            className="ui-input ui-input--block"
          />
          {error && <div className="alert alert--error">{error}</div>}
          <button type="submit" disabled={loading} className="button button--success">
            {loading ? 'Загрузка…' : mode === 'login' ? 'Войти' : 'Зарегистрироваться'}
          </button>
        </form>

        <div className="login-divider">или</div>
        <a href="/api/auth/moysklad/login" className="button" style={{ display: 'block', textAlign: 'center' }}>
          Войти через МойСклад
        </a>
      </div>
    </div>
  )
}
