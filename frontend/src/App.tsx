import { BrowserRouter, Routes, Route, Navigate, NavLink, Link } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AcceptancePage } from './pages/Acceptance'
import { SettingsPage } from './pages/Settings'
import { LoginPage } from './pages/Login'
import { useScanStore } from './store/scanStore'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
})

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = localStorage.getItem('access_token')
  if (!token) return <Navigate to="/login" replace />
  return children
}

function Layout({ children }: { children: React.ReactNode }) {
  const czTokenExpired = useScanStore((s) => s.czTokenExpired)
  const setCzTokenExpired = useScanStore((s) => s.setCzTokenExpired)

  const handleLogout = () => {
    localStorage.removeItem('access_token')
    window.location.href = '/login'
  }

  return (
    <>
      {czTokenExpired && (
        <div className="top-banner" role="alert">
          <span>Токен Честного Знака истёк. Войдите заново через УКЭП.</span>
          <Link to="/settings" onClick={() => setCzTokenExpired(false)}>
            Открыть настройки
          </Link>
          <button
            type="button"
            className="top-banner__close"
            onClick={() => setCzTokenExpired(false)}
            aria-label="Закрыть"
          >
            ×
          </button>
        </div>
      )}
      {children}
      <nav className="app-nav" aria-label="Навигация">
        <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>
          Приёмка
        </NavLink>
        <NavLink to="/settings" className={({ isActive }) => (isActive ? 'active' : '')}>
          Настройки
        </NavLink>
        <span className="app-nav__sep" />
        <button type="button" onClick={handleLogout}>Выйти</button>
      </nav>
    </>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <Layout>
                  <AcceptancePage />
                </Layout>
              </RequireAuth>
            }
          />
          <Route
            path="/settings"
            element={
              <RequireAuth>
                <Layout>
                  <SettingsPage />
                </Layout>
              </RequireAuth>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
