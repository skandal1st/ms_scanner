import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, NavLink, Link, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { acceptanceApi } from './api/client'
import { Icon } from './components/Icon'
import type { IconName } from './components/Icon'
import { ModalProvider } from './components/ModalProvider'
import { ShipmentPage } from './pages/Shipment'
import { AcceptancePage } from './pages/Acceptance'
import { WriteoffPage } from './pages/Writeoff'
import { CzCheckPage } from './pages/CzCheck'
import { MarkControlPage } from './pages/MarkControl'
import { InventoryPage } from './pages/Inventory'
import { PhysicalCounts } from './components/PhysicalCounts'
import { SettingsPage } from './pages/Settings'
import { HelpPage } from './pages/Help'
import { LoginPage } from './pages/Login'
import { MsIframePage } from './pages/MsIframe'
import { LaunchPage } from './pages/Launch'
import { PopupPage } from './pages/Popup'
import { useEdition } from './hooks/useEdition'
import { OrganizationProfileSwitcher } from './components/OrganizationProfileSwitcher'
import { NavDropdown } from './components/NavDropdown'
import { enforceComScannerMode } from './lib/scannerMode'

const TsdPage = lazy(() => import('./pages/Tsd').then((module) => ({ default: module.TsdPage })))
const GtinControlPage = lazy(() => import('./pages/GtinControl').then((module) => ({ default: module.GtinControlPage })))

// Одноразово мигрируем старую локальную настройку «USB-клавиатура» в обязательный COM.
enforceComScannerMode()

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
})

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = localStorage.getItem('access_token')
  if (!token) return <Navigate to="/login" replace />
  return children
}

/** Гейт full-only разделов: ms_lite → редирект на отгрузку (плюс серверный 403). */
function RequireFull({ children }: { children: JSX.Element }) {
  const { isFull, isLoading } = useEdition()
  if (isLoading) return null
  if (!isFull) return <Navigate to="/shipment" replace />
  return children
}

function InventoryRoute() {
  const { isFull, isLoading } = useEdition()
  if (isLoading) return null
  const count = <main className="mc">
    {isFull && <Link to="/inventory">‹ Инвентаризация</Link>}
    <PhysicalCounts mode="inventory" defaultCountMethod="scan" />
  </main>
  return <Routes>
    <Route index element={isFull ? <main className="mc">
      <h1 className="mc-head__title">Инвентаризация</h1>
      <div className="inventory-choices">
        <Link to="count" className="card">
          <strong>Пересчитать товары на ТСД</strong>
          <span>Выберите склад и категории для пересчёта. Здесь же можно открыть результат.</span>
        </Link>
        <Link to="reconcile" className="card">
          <strong>Сверить остатки ЧЗ и МойСклада</strong>
          <span>Сравните учётные остатки и найдите товары с расхождениями.</span>
        </Link>
      </div>
    </main> : count} />
    <Route path="count" element={count} />
    <Route path="reconcile" element={isFull ? <InventoryPage /> : <Navigate to="/inventory" replace />} />
    <Route path="*" element={<Navigate to="/inventory" replace />} />
  </Routes>
}

const NAV_ITEMS: { to: string; label: string; icon: IconName }[] = [
  { to: '/shipment', label: 'Отгрузка', icon: 'shipment' },
  { to: '/acceptance', label: 'Приёмка', icon: 'acceptance' },
  { to: '/writeoff', label: 'Списание', icon: 'writeoff' },
  { to: '/inventory', label: 'Инвентаризация', icon: 'check' },
  { to: '/check', label: 'Проверить код', icon: 'scan' },
]

function Layout({ children }: { children: React.ReactNode }) {
  const { isFull, isLoading } = useEdition()
  const fullNavigation = isFull && !isLoading
  const { pathname } = useLocation()

  const handleLogout = () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user_id')
    localStorage.removeItem('organization_profile_id')
    window.location.href = '/login'
  }

  // Бейдж «новых входящих УПД» на вкладке «Приёмка» — мониторинг ЭДО (опрос раз в минуту).
  // ЭДО — только полная версия, в ms_lite эндпоинт отдаёт 403, поэтому не опрашиваем.
  const { data: edoCount = 0 } = useQuery({
    queryKey: ['edo-incoming-count'],
    queryFn: () => acceptanceApi.edoIncomingCount().then((r) => r.data.count),
    refetchInterval: 60_000,
    staleTime: 30_000,
    enabled: fullNavigation,
  })

  return (
    <>
      <nav className="app-nav" aria-label="Навигация">
        <span className="app-nav__brand">
          <img src="/logo-light.svg" alt="Скандата" className="app-nav__logo-img" />
        </span>
        <div className="app-nav__links">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => (isActive ? 'active' : '')}
          >
            <Icon name={item.icon} size={17} className="app-nav__icon" />
            {item.label}
            {item.to === '/acceptance' && fullNavigation && edoCount > 0 && (
              <span
                className="badge badge--info"
                style={{ marginLeft: 6, fontSize: 11, padding: '0 6px', borderRadius: 10 }}
                title={`Новых УПД с маркировкой: ${edoCount}`}
              >
                {edoCount}
              </span>
            )}
          </NavLink>
        ))}
        </div>
        {fullNavigation && <NavDropdown label="Контроль" className="app-nav__control"
          active={pathname === '/mark-control' || pathname === '/gtin-control'}>
          <NavLink to="/mark-control">Контроль марок</NavLink>
          <NavLink to="/gtin-control">Контроль GTIN</NavLink>
        </NavDropdown>}
        <OrganizationProfileSwitcher compact activePage={pathname === '/settings' || pathname === '/help'}>
          <NavLink to="/settings">Настройки</NavLink>
          <NavLink to="/help">Помощь</NavLink>
          <button type="button" onClick={handleLogout}>Выйти</button>
        </OrganizationProfileSwitcher>
      </nav>
      {children}
    </>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ModalProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/ms" element={<MsIframePage />} />
          <Route path="/launch" element={<LaunchPage />} />
          {/* Кастомное модальное окно МС (кнопка на документе): авторизуется по contextKey. */}
          <Route path="/popup" element={<PopupPage />} />
          <Route path="/tsd" element={<Suspense fallback={null}><TsdPage /></Suspense>} />
          {/* Киоск-окна сканирования — внешняя вкладка, открытая из МС (/launch → сюда).
              Без Layout-навигации: кладовщик заперт на своей задаче и не уходит в
              приёмку/инвентаризацию/настройки. Функции самой задачи (выбор документа,
              «Следующая отгрузка») остаются. */}
          <Route
            path="/scan/shipment"
            element={
              <RequireAuth>
                <div className="scan-kiosk">
                  <ShipmentPage />
                </div>
              </RequireAuth>
            }
          />
          <Route
            path="/scan/writeoff"
            element={
              <RequireAuth>
                <div className="scan-kiosk">
                  <WriteoffPage />
                </div>
              </RequireAuth>
            }
          />
          <Route
            path="/scan/acceptance"
            element={
              <RequireAuth>
                <div className="scan-kiosk">
                  <AcceptancePage />
                </div>
              </RequireAuth>
            }
          />
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
                <RequireFull>
                  <Layout>
                    <MarkControlPage />
                  </Layout>
                </RequireFull>
              </RequireAuth>
            }
          />
          <Route path="/gtin-control" element={
            <RequireAuth><RequireFull><Layout>
              <Suspense fallback={<div className="gtin-control">Загружаем контроль GTIN…</div>}>
                <GtinControlPage />
              </Suspense>
            </Layout></RequireFull></RequireAuth>
          } />
          <Route
            path="/inventory/*"
            element={
              <RequireAuth>
                  <Layout>
                    <InventoryRoute />
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
      </ModalProvider>
    </QueryClientProvider>
  )
}
