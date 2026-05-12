import { BrowserRouter, Routes, Route, Navigate, NavLink } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AcceptancePage } from './pages/Acceptance'
import { ShipmentPage } from './pages/Shipment'
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
    window.location.href = '/login'
  }

  return (
    <>
      {children}
      <nav className="app-nav" aria-label="Навигация">
        <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>
          Приёмка
        </NavLink>
        <NavLink to="/shipment" className={({ isActive }) => (isActive ? 'active' : '')}>
          Отгрузка
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
          <Route path="/ms" element={<MsIframePage />} />
          <Route path="/launch" element={<LaunchPage />} />
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
