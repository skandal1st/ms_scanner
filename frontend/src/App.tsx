import { BrowserRouter, Routes, Route, Navigate, NavLink } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ShipmentPage } from './pages/Shipment'
import { WriteoffPage } from './pages/Writeoff'
import { SettingsPage } from './pages/Settings'
import { LoginPage } from './pages/Login'
import { MsIframePage } from './pages/MsIframe'
import { LaunchPage } from './pages/Launch'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
})

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = localStorage.getItem('access_token')
  if (!token) return <Navigate to="/login" replace />
  return children
}

function Layout({ children }: { children: React.ReactNode }) {
  const handleLogout = () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user_id')
    window.location.href = '/login'
  }

  return (
    <>
      <nav className="app-nav" aria-label="Навигация">
        <NavLink to="/shipment" className={({ isActive }) => (isActive ? 'active' : '')}>
          Отгрузка
        </NavLink>
        <NavLink to="/writeoff" className={({ isActive }) => (isActive ? 'active' : '')}>
          Списание
        </NavLink>
        <NavLink to="/settings" className={({ isActive }) => (isActive ? 'active' : '')}>
          Настройки
        </NavLink>
        <span className="app-nav__sep" />
        <button type="button" onClick={handleLogout}>Выйти</button>
      </nav>
      {children}
    </>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/ms" element={<MsIframePage />} />
          <Route path="/launch" element={<LaunchPage />} />
          <Route path="/" element={<Navigate to="/shipment" replace />} />
          <Route
            path="/shipment"
            element={
              <RequireAuth>
                <Layout>
                  <ShipmentPage />
                </Layout>
              </RequireAuth>
            }
          />
          <Route
            path="/writeoff"
            element={
              <RequireAuth>
                <Layout>
                  <WriteoffPage />
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
