import { BrowserRouter, Routes, Route, Navigate, NavLink } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Icon } from './components/Icon'
import type { IconName } from './components/Icon'
import { ShipmentPage } from './pages/Shipment'
import { AcceptancePage } from './pages/Acceptance'
import { WriteoffPage } from './pages/Writeoff'
import { CzCheckPage } from './pages/CzCheck'
import { MarkControlPage } from './pages/MarkControl'
import { InventoryPage } from './pages/Inventory'
import { SettingsPage } from './pages/Settings'
import { HelpPage } from './pages/Help'
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

const NAV_ITEMS: { to: string; label: string; icon: IconName }[] = [
  { to: '/shipment', label: 'Отгрузка', icon: 'shipment' },
  { to: '/acceptance', label: 'Приёмка', icon: 'acceptance' },
  { to: '/writeoff', label: 'Списание', icon: 'writeoff' },
  { to: '/check', label: 'Проверка', icon: 'check' },
  { to: '/mark-control', label: 'Контроль марок', icon: 'check' },
  { to: '/inventory', label: 'Инвентаризация', icon: 'check' },
  { to: '/settings', label: 'Настройки', icon: 'settings' },
  { to: '/help', label: 'Помощь', icon: 'help' },
]

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
        <span className="app-nav__brand">
          <img src="/logo-light.svg" alt="Скандата" className="app-nav__logo-img" />
        </span>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => (isActive ? 'active' : '')}
          >
            <Icon name={item.icon} size={17} className="app-nav__icon" />
            {item.label}
          </NavLink>
        ))}
        <span className="app-nav__spacer" />
        <button type="button" onClick={handleLogout}>
          <Icon name="logout" size={16} className="app-nav__icon" />
          Выйти
        </button>
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
            path="/acceptance"
            element={
              <RequireAuth>
                <Layout>
                  <AcceptancePage />
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
            path="/check"
            element={
              <RequireAuth>
                <Layout>
                  <CzCheckPage />
                </Layout>
              </RequireAuth>
            }
          />
          <Route
            path="/mark-control"
            element={
              <RequireAuth>
                <Layout>
                  <MarkControlPage />
                </Layout>
              </RequireAuth>
            }
          />
          <Route
            path="/inventory"
            element={
              <RequireAuth>
                <Layout>
                  <InventoryPage />
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
          <Route
            path="/help"
            element={
              <RequireAuth>
                <Layout>
                  <HelpPage />
                </Layout>
              </RequireAuth>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
