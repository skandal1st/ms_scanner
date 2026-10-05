import { useDeferredValue, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import axios from 'axios'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { tsdApi, type TsdDocumentDetail, type TsdDocumentItem, type TsdOrderShipments } from '../api/client'
import { CustomerOrderFilterSelect } from '../components/CustomerOrderFilterSelect'
import { OrderStatusBadge } from '../components/OrderStatusBadge'
import { buildProgress, effectiveGtinKey, findProgressRowForScan, scanUnits, progressAfterScan } from '../store/scanStore'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { TsdPwaControls, TsdConnection, useTsdOnline } from '../components/TsdPwaControls'
import { useTsdSound } from '../hooks/useTsdSound'
import { useTsdScannerFocus } from '../hooks/useTsdScannerFocus'
import { useDocumentLive, type DocumentEvent } from '../hooks/useDocumentLive'
import { applyScanEvent } from '../lib/scanEvents'
import { parseTsdDocumentCode } from '../lib/tsdLinks'
import { Icon } from '../components/Icon'
import { scanPackageLabel, scanPackageType } from '../lib/scanPackaging'

function progressState(added: number, expected: number) {
  return expected > 0 && added > expected ? 'overflow'
    : expected > 0 && added === expected ? 'done' : added > 0 ? 'partial' : 'empty'
}
const progressLabels = { empty: 'Не собрано', partial: 'В сборке', done: 'Собрано', overflow: 'Сверх плана' }

function apiMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (!error.response) return 'Нет ответа от сервера. Проверьте связь и список марок перед повторным сканированием.'
    return String(error.response?.data?.detail || 'Не удалось выполнить операцию')
  }
  return error instanceof Error ? error.message : 'Не удалось выполнить операцию'
}

function normalizePairingCode(raw: string): string {
  const value = raw.trim()
  if (value.startsWith('SKANDATA:TSD:')) return value
  try {
    const url = new URL(value)
    return url.searchParams.get('pair') || value
  } catch {
    return value
  }
}

function documentCode(raw: string): string | null {
  return parseTsdDocumentCode(raw)
}

function TsdLogin({ onReady }: { onReady: () => void }) {
  const online = useTsdOnline()
  const autoExchanged = useRef(false)
  const [code, setCode] = useState(() => new URLSearchParams(window.location.search).get('pair') || '')
  const [name, setName] = useState(() => localStorage.getItem('tsd_device_name') || 'ТСД АТОЛ')
  const inputRef = useRef<HTMLInputElement>(null)
  const exchange = useMutation({
    networkMode: 'always',
    mutationFn: () => tsdApi.exchange(normalizePairingCode(code), name).then((r) => r.data),
    onSuccess: (data) => {
      localStorage.setItem('tsd_access_token', data.access_token)
      localStorage.setItem('tsd_device_name', data.device_name)
      window.history.replaceState({}, '', '/tsd')
      onReady()
    },
  })
  useEffect(() => {
    inputRef.current?.focus()
    if (online && !autoExchanged.current && code && new URLSearchParams(window.location.search).get('pair')) {
      autoExchanged.current = true
      exchange.mutate()
    }
    // Автообмен нужен только при первом открытии app-link.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (online && code.trim() && name.trim() && !exchange.isPending) exchange.mutate()
  }
  return (
    <main className="tsd-shell tsd-login">
      <div className="tsd-login__mark">S</div>
      <h1>Подключение ТСД</h1>
      <p>Откройте QR подключения в настройках Скандаты и отсканируйте его аппаратной кнопкой.</p>
      <form onSubmit={submit}>
        <label>
          Имя устройства
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={255} />
        </label>
        <label>
          QR-код подключения
          <input
            ref={inputRef}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="Нажмите кнопку сканирования"
            autoComplete="off"
          />
        </label>
        {exchange.error ? <div className="tsd-alert tsd-alert--error">{apiMessage(exchange.error)}</div> : null}
        <button type="submit" className="tsd-button tsd-button--primary" disabled={!online || exchange.isPending || !code.trim() || !name.trim()}>
          {exchange.isPending ? 'Подключаем…' : 'Подключить устройство'}
        </button>
      </form>
    </main>
  )
}

function ShipmentRow({ item, onOpen, disabled }: { item: TsdDocumentItem; onOpen: () => void; disabled?: boolean }) {
  const progress = item.expected > 0 ? Math.min(100, Math.round(item.collected * 100 / item.expected)) : 0
  return (
    <button type="button" className="tsd-shipment-row" onClick={onOpen} disabled={disabled}>
      <div className="tsd-shipment-row__main">
        <strong>{item.name}</strong>
        <span>{item.agent_name || item.customer_order_name || 'Контрагент не указан'}</span>
        <small>{item.store_name || 'Склад не указан'}</small>
      </div>
      <div className="tsd-shipment-row__progress">
        <time>{item.moment ? new Date(item.moment).toLocaleDateString('ru-RU') : '—'}</time>
        <b>{item.collected}/{item.expected || '—'}</b>
        <span><i style={{ width: `${progress}%` }} /></span>
        {item.active_on_other_device ? <em>Открыта на другом ТСД</em> : null}
      </div>
      <span className="tsd-chevron" aria-hidden>›</span>
    </button>
  )
}

function TsdOrderList({ onOpen }: { onOpen: (doc: TsdDocumentDetail) => void }) {
  const online = useTsdOnline()
  const [search, setSearch] = useState('')
  const [filterId, setFilterId] = useState(() => localStorage.getItem('tsd_order_filter_id') || '')
  const filters = useQuery({ queryKey: ['tsd-order-filters'], queryFn: () => tsdApi.orderFilters().then(r => r.data), refetchInterval: 30_000 })
  const activeFilterId = filters.data?.some(f => f.id === filterId) ? filterId : ''
  const deferredSearch = useDeferredValue(search)
  const [tab, setTab] = useState<'available' | 'work'>('available')
  const [qrMode, setQrMode] = useState(false)
  const [qrValue, setQrValue] = useState('')
  const [openError, setOpenError] = useState<string | null>(null)
  const [orderChoice, setOrderChoice] = useState<TsdOrderShipments | null>(null)
  const qrRef = useRef<HTMLInputElement>(null)
  const { data: me } = useQuery({ queryKey: ['tsd-me'], queryFn: () => tsdApi.me().then((r) => r.data) })
  const { data: orderPages, isLoading, error: listError, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ['tsd-orders', deferredSearch, activeFilterId], enabled: !filters.isLoading,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => tsdApi.orders(deferredSearch.trim(), pageParam, activeFilterId).then((r) => r.data),
    getNextPageParam: (last, pages) => last.length === 50 ? pages.length * 50 : undefined,
  })
  const select = useMutation({
    networkMode: 'always',
    mutationFn: (value: { id: string; orderId?: string }) => tsdApi.selectDocument(value.id, value.orderId).then((r) => r.data),
    onSuccess: onOpen,
    onError: (error) => setOpenError(apiMessage(error)),
  })
  const chooseOrder = useMutation({
    networkMode: 'always',
    mutationFn: (id: string) => tsdApi.orderShipments(id).then((r) => r.data),
    onMutate: () => { setOpenError(null) },
    onSuccess: (result) => {
      if (result.shipments.length === 1) select.mutate({ id: result.shipments[0].moysklad_id, orderId: result.order_id })
      else setOrderChoice(result)
    },
    onError: (error) => setOpenError(apiMessage(error)),
  })
  useEffect(() => {
    if (qrMode) qrRef.current?.focus()
  }, [qrMode])
  const filtered = (orderPages?.pages.flat() || []).filter((item) => tab === 'work' ? item.in_work : !item.in_work)
  const submitQr = (event: FormEvent) => {
    event.preventDefault()
    const id = documentCode(qrValue)
    if (!id) {
      setOpenError('Это не QR отгрузки Скандаты')
      return
    }
    if (online && !select.isPending && !chooseOrder.isPending) select.mutate({ id })
  }
  return (
    <main className="tsd-shell">
      <header className="tsd-header">
        <div>
          <h1>{orderChoice ? `Заказ ${orderChoice.order_name}` : 'Заказы покупателей'}</h1>
          <p>{me?.workplace_name || 'Рабочее место'} · {me?.organization_name || 'Юрлицо'}</p>
        </div>
        <TsdConnection />
      </header>
      {orderChoice ? <section className="tsd-order-choice">
        <button type="button" className="tsd-button" disabled={select.isPending || chooseOrder.isPending} onClick={() => { setOrderChoice(null); setOpenError(null) }}>К списку заказов</button>
        <h2>Выберите отгрузку</h2>
        {orderChoice.shipments.length === 0 && <p className="tsd-alert tsd-alert--warn">{orderChoice.empty_shipments_message || 'Нет доступных отгрузок. Проверьте связанные документы в МойСкладе.'}</p>}
        {orderChoice.shipments.map((item) => <ShipmentRow key={item.moysklad_id} item={item} disabled={!online || select.isPending || chooseOrder.isPending} onOpen={() => {
          if (online && !select.isPending) { setOpenError(null); select.mutate({ id: item.moysklad_id, orderId: orderChoice.order_id }) }
        }} />)}
        <button type="button" className="tsd-button" disabled={!online || select.isPending || chooseOrder.isPending} onClick={() => chooseOrder.mutate(orderChoice.order_id)}>Обновить отгрузки</button>
        {openError && <div className="tsd-alert tsd-alert--error">{openError}</div>}
        {select.isPending && <p role="status">Открываем сборку…</p>}
      </section> : <>
      <div className="tsd-list-controls">
        <CustomerOrderFilterSelect tsd filters={filters.data || []} value={activeFilterId} onChange={id => { setFilterId(id); localStorage.setItem('tsd_order_filter_id', id) }} disabled={!online || filters.isLoading || select.isPending || chooseOrder.isPending || !!filters.error} />
        {filters.error && <div className="tsd-alert tsd-alert--error">Не удалось загрузить фильтры. <button type="button" className="tsd-button" disabled={!online} onClick={() => filters.refetch()}>Повторить</button></div>}
        <label className="tsd-search">
          <span aria-hidden>⌕</span>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Найти номер или контрагента" />
        </label>
        <button type="button" className="tsd-button tsd-button--primary" onClick={() => setQrMode((v) => !v)}>
          Сканировать QR
        </button>
        {qrMode ? (
          <form className="tsd-qr-capture" onSubmit={submitQr}>
            <input ref={qrRef} value={qrValue} onChange={(e) => setQrValue(e.target.value)} placeholder="Сканируйте QR отгрузки" />
            <button type="submit" className="tsd-button tsd-button--primary">Открыть</button>
          </form>
        ) : null}
      </div>
      <div className="tsd-tabs" role="tablist">
        <button className={tab === 'available' ? 'active' : ''} onClick={() => setTab('available')}>Доступные</button>
        <button className={tab === 'work' ? 'active' : ''} onClick={() => setTab('work')}>В работе</button>
      </div>
      {listError ? <div className="tsd-alert tsd-alert--error">{apiMessage(listError)} <button type="button" className="tsd-button" onClick={() => refetch()} disabled={!online}>Повторить</button></div> : null}
      {openError ? <div className="tsd-alert tsd-alert--error">{openError}</div> : null}
      <section className="tsd-shipment-list" aria-live="polite">
        {isLoading ? <p className="tsd-empty">Загружаем заказы покупателей…</p> : null}
        {chooseOrder.isPending || select.isPending ? <p role="status" className="tsd-empty">Открываем отгрузки заказа…</p> : null}
        {!isLoading && !listError && filtered.length === 0 ? (
          <div className="tsd-empty">
            <p>{tab === 'work' ? 'Нет начатых сборок на этой странице' : 'Нет доступных заказов на этой странице'}</p>
            <button type="button" className="tsd-button" onClick={() => refetch()}>Обновить</button>
          </div>
        ) : null}
        {filtered.map((item) => (
          <button key={item.moysklad_id} type="button" className="tsd-order-row" disabled={!online || select.isPending || chooseOrder.isPending} onClick={() => chooseOrder.mutate(item.moysklad_id)}>
            <div className="tsd-order-row__heading">
              <strong>Заказ {item.name}</strong>
              <time dateTime={item.moment || undefined}>{item.moment ? new Date(item.moment).toLocaleDateString('ru-RU') : '—'}</time>
            </div>
            <div className="tsd-order-row__details">
              <span className="tsd-order-row__agent">{item.agent_name || 'Контрагент не указан'}</span>
              <OrderStatusBadge name={item.state_name} color={item.state_color} />
            </div>
            <small className="tsd-order-row__shipments">
              {item.shipment_count === null ? 'Проверить отгрузки' : item.shipment_count ? `Отгрузок: ${item.shipment_count}` : item.retail_sale_count ? 'Розничная продажа' : item.shipment_count === 0 ? 'Нет отгрузки' : 'Открыть'}
              {item.store_name ? ` · ${item.store_name}` : ''}
            </small>
          </button>
        ))}
      </section>
      {hasNextPage && <button type="button" className="tsd-button tsd-more-orders" disabled={!online || isFetchingNextPage} onClick={() => fetchNextPage()}>{isFetchingNextPage ? 'Загружаем…' : 'Показать ещё заказы'}</button>}
      </>}
    </main>
  )
}

function TsdPicking({ initial, onBack }: { initial: TsdDocumentDetail; onBack: () => void }) {
  const online = useTsdOnline()
  const sound = useTsdSound()
  const qc = useQueryClient()
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [code, setCode] = useState('')
  const [lastCode, setLastCode] = useState('')
  const [reviewing, setReviewing] = useState(false)
  const [positionFilter, setPositionFilter] = useState<number | 'all' | 'other'>('all')
  const [selectedScanId, setSelectedScanId] = useState<string | null>(null)
  const [visibleScans, setVisibleScans] = useState(50)
  const [targetProductId, setTargetProductId] = useState<string | null>(null)
  const submitting = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const reviewRef = useRef<HTMLDetailsElement>(null)
  const liveDuringFetch = useRef<DocumentEvent[] | null>(null)
  const { data: doc = initial } = useQuery({
    queryKey: ['tsd-document', initial.id],
    queryFn: async () => {
      const events: DocumentEvent[] = []
      liveDuringFetch.current = events
      try {
        const { data } = await tsdApi.getDocument(initial.id)
        return { ...data, scans: events.reduce(applyScanEvent, data.scans) }
      } finally { if (liveDuringFetch.current === events) liveDuringFetch.current = null }
    },
    initialData: initial,
    refetchInterval: (query) => query.state.data?.scans.some((item) => item.status === 'pending') ? 1500 : false,
  })
  const progress = useMemo(() => buildProgress(doc.plan, doc.scans), [doc.plan, doc.scans])
  const last = doc.scans[0]
  const rowForScan = (value: typeof last) => findProgressRowForScan(value, progress.rows)
  const target = progress.rows.find((row) => row.product_id === targetProductId && targetProductId)
  const current = target || (last && rowForScan(last)) || progress.rows.find((row) => row.addedTotal < row.expected) || progress.rows[0]
  const selectedScan = doc.scans.find((item) => item.id === selectedScanId)
  const filteredScans = doc.scans.filter((item) => positionFilter === 'all'
    || (positionFilter === 'other' ? !rowForScan(item) : rowForScan(item) === progress.rows[positionFilter]))
  const selectPosition = (value: typeof positionFilter) => {
    setPositionFilter(value)
    setSelectedScanId(null)
    setVisibleScans(50)
  }
  const percent = progress.total.expected > 0
    ? Math.min(100, Math.round(progress.total.addedTotal * 100 / progress.total.expected))
    : 0
  const refresh = () => qc.invalidateQueries({ queryKey: ['tsd-document', initial.id] })
  useDocumentLive(initial.id, true, (event) => {
    liveDuringFetch.current?.push(event)
    if (event.type === 'scans_changed' || (event.type === 'scan_update' && !qc.getQueryData<TsdDocumentDetail>(['tsd-document', initial.id])?.scans.some(item => item.id === event.scan_id))) {
      void refresh(); return
    }
    if (!['scan_upsert', 'scan_removed', 'scans_reset', 'scan_update'].includes(event.type)) return
    qc.setQueryData<TsdDocumentDetail>(['tsd-document', initial.id], previous => previous
      ? { ...previous, scans: applyScanEvent(previous.scans, event) } : previous)
  }, () => { void refresh() })
  const scan = useMutation({
    networkMode: 'always',
    mutationFn: (value: { code: string; productId?: string }) => tsdApi.scan(doc.id, value.code, value.productId).then((r) => r.data),
    onSuccess: (result) => {
      const rejected = ['invalid', 'used_in_other_doc', 'unknown_product'].includes(result.status)
      const latest = qc.getQueryData<TsdDocumentDetail>(['tsd-document', initial.id]) || doc
      const { scans: mergedScans, matched, overPlan } = progressAfterScan(latest.plan, latest.scans, result)
      const errorFeedback = Boolean(result.duplicate || rejected || !matched || overPlan)
      setMessage(result.duplicate
        ? { kind: 'error', text: 'Этот код уже отсканирован' }
        : rejected ? { kind: 'error', text: result.error_message || 'Не удалось распознать марку' }
        : !matched ? { kind: 'error', text: `Скан записан, но GTIN ${effectiveGtinKey(result) || 'не определён'} отсутствует в плане отгрузки` }
        : overPlan ? { kind: 'error', text: `Марка добавлена сверх плана: ${matched.product_name}` }
        : { kind: 'ok', text: result.product_name || matched.product_name || 'Скан принят' })
      qc.setQueryData<TsdDocumentDetail>(['tsd-document', initial.id], { ...latest, scans: mergedScans })
      setCode('')
      refresh()
      sound.play(errorFeedback ? 'error' : 'ok')
      window.navigator.vibrate?.(errorFeedback ? [80, 60, 80] : 60)
      inputRef.current?.focus({ preventScroll: true })
    },
    onError: (error) => {
      setMessage({ kind: 'error', text: apiMessage(error) })
      setCode('')
      window.navigator.vibrate?.([120, 80, 120])
      sound.play('error')
      inputRef.current?.focus({ preventScroll: true })
    },
    onSettled: () => { submitting.current = false },
  })
  const undo = useMutation({
    networkMode: 'always',
    mutationFn: () => tsdApi.undoLast(doc.id),
    onSuccess: () => { setMessage(null); refresh() },
    onError: (error) => { setMessage({ kind: 'error', text: apiMessage(error) }); sound.play('error') },
  })
  const remove = useMutation({
    networkMode: 'always',
    mutationFn: (scanId: string) => tsdApi.deleteScan(doc.id, scanId).then((r) => r.data),
    onMutate: () => qc.cancelQueries({ queryKey: ['tsd-document', initial.id] }),
    onSuccess: (removed) => {
      qc.setQueryData<TsdDocumentDetail>(['tsd-document', initial.id], (previous) => previous
        ? { ...previous, scans: previous.scans.filter((item) => item.id !== removed.id) } : previous)
      setSelectedScanId(null)
      setMessage({ kind: 'ok', text: 'Выбранная марка удалена из сборки' })
      refresh()
    },
    onError: (error) => { setMessage({ kind: 'error', text: apiMessage(error) }); sound.play('error') },
  })
  const complete = useMutation({
    networkMode: 'always',
    mutationFn: () => tsdApi.complete(doc.id),
    onSuccess: onBack,
    onError: (error) => { setMessage({ kind: 'error', text: apiMessage(error) }); sound.play('error') },
  })
  const packMode = useMutation({
    mutationFn: (item: typeof last) => tsdApi.packMode(doc.id, item.id, Boolean(item.keep_aggregate || !item.child_codes?.length)),
    onSuccess: ({ data }) => {
      setMessage({ kind: 'ok', text: data.status === 'pending' ? 'Получаем состав блока из Честного Знака…' : scanPackageLabel(data) })
      refresh()
    },
    onError: (error) => { setMessage({ kind: 'error', text: apiMessage(error) }); sound.play('error') },
  })
  useEffect(() => {
    // Android scanners may inject through the IME and need an editable, focused input.
    // Restore focus after React applies mode changes or removes readOnly after a request.
    if (!scan.isPending && !undo.isPending && !complete.isPending && !remove.isPending) {
      inputRef.current?.focus({ preventScroll: true })
    }
  }, [targetProductId, scan.isPending, undo.isPending, complete.isPending, remove.isPending])
  const scannerBlocked = scan.isPending || undo.isPending || complete.isPending || remove.isPending || packMode.isPending
  useTsdScannerFocus(inputRef, scannerBlocked, (value) => setCode((previous) => previous + value))
  const acceptCode = () => {
    const normalized = normalizeScannerInput(code).trim()
    if (!normalized || submitting.current || scan.isPending || complete.isPending || undo.isPending || remove.isPending) return
    if (!online) {
      setMessage({ kind: 'error', text: 'Нет сети. Подключитесь к Wi-Fi и повторите скан.' })
      sound.play('error')
      return
    }
    if (targetProductId && !target) {
      setMessage({ kind: 'error', text: 'Выбранная позиция больше недоступна. Включите автоматический выбор.' })
      sound.play('error')
      return
    }
    submitting.current = true
    setLastCode(normalized)
    setMessage(null)
    scan.mutate({ code: normalized, productId: target?.product_id || undefined })
  }
  const acceptRef = useRef(acceptCode)
  acceptRef.current = acceptCode
  useEffect(() => {
    // Keyboard-wedge scanners may paste an entire code without an Enter suffix.
    // No offline or pending scan queue; the positions list never blocks the scanner.
    if (!code.trim() || !online || scan.isPending || undo.isPending || complete.isPending || remove.isPending) return
    const timer = window.setTimeout(() => acceptRef.current(), 500)
    return () => window.clearTimeout(timer)
  }, [code, online, scan.isPending, undo.isPending, complete.isPending, remove.isPending])
  const submit = (event: FormEvent) => { event.preventDefault(); acceptCode() }
  return (
    <main className="tsd-shell tsd-picking">
      <section className="tsd-scanner-dock" aria-label="Сканер маркировки">
        <form className={`tsd-scan-box tsd-scan-box--compact ${message ? `tsd-scan-box--${message.kind}` : ''}`} onSubmit={submit}>
          <label htmlFor="tsd-scan-input"><Icon name="scan" size={20} /><span className="tsd-visually-hidden">Сканируйте штрихкод</span></label>
          <input id="tsd-scan-input" ref={inputRef} value={code} onChange={(e) => setCode(e.target.value)}
            readOnly={scannerBlocked} inputMode="text" placeholder={scan.isPending ? 'Записываем скан…' : 'Сканируйте марку'}
            autoComplete="off" autoCapitalize="off" spellCheck={false} enterKeyHint="send" />
          <button type="submit" className="tsd-button" aria-label="Принять код" disabled={!online || !code.trim() || scannerBlocked}><Icon name="check" size={20} /></button>
        </form>
        <div className="tsd-dock-meta">
          <span title={target?.product_name}>{targetProductId ? `Марки → ${target?.product_name || 'Позиция недоступна'}` : 'Авто по GTIN'}</span>
          {targetProductId && <button type="button" className="tsd-button" disabled={scannerBlocked || Boolean(code.trim())} onClick={() => setTargetProductId(null)}>Авто</button>}
          <button type="button" data-tsd-sound className="tsd-button" aria-label={sound.active ? 'Отключить звук' : 'Включить звук'} aria-pressed={sound.active} onClick={() => void sound.toggle()}>{sound.active ? 'Звук вкл.' : 'Звук выкл.'}</button>
        </div>
        {message && <div role="status" aria-live="polite" className={`tsd-dock-message tsd-dock-message--${message.kind}`}>{message.text}</div>}
      </section>
      <header className="tsd-header tsd-header--picking">
        <button type="button" className="tsd-back" onClick={onBack} aria-label="Назад">‹</button>
        <div><h1>Сборка заказа</h1><p>{doc.customer_order_name ? `Заказ ${doc.customer_order_name} · Отгрузка ${doc.name}` : doc.name}</p></div>
        <TsdConnection />
      </header>
      {doc.active_on_other_device ? <div className="tsd-alert tsd-alert--warn">Отгрузка также открыта на другом ТСД</div> : null}
      <section className="tsd-total">
        <span>Собрано</span>
        <strong>{progress.total.addedTotal}<small>/{progress.total.expected || '—'}</small></strong>
        <b>{percent}%</b>
        <div><i style={{ width: `${percent}%` }} /></div>
      </section>
      <section className={`tsd-current tsd-progress--${progressState(current?.addedTotal || 0, current?.expected || 0)}`}>
        <div><span>{target ? 'Выбранный товар' : 'Текущий товар'}</span><small>{progress.rows.indexOf(current) + 1} из {progress.rows.length}</small></div>
        <h2>{current?.product_name || 'Сканируйте товар'}</h2>
        {last && <p className="hint">GTIN последнего скана: {effectiveGtinKey(last) || 'не распознан'}</p>}
        <div className="tsd-counts">
          <p><span>Ожидалось</span><b>{current?.expected ?? '—'} <small>шт.</small></b></p>
          <p><span>Собрано</span><b>{current?.addedTotal ?? 0} <small>шт.</small></b></p>
        </div>
      </section>
      <details ref={reviewRef} className="tsd-order-review" onToggle={(event) => setReviewing(event.currentTarget.open)}>
        <summary>Позиции заказа ({progress.rows.length}) и сканы ({doc.scans.length})</summary>
        <p className="hint">Выберите позицию для привязки следующих марок или марку для удаления.</p>
        {reviewing && <>
          <div className="tsd-position-list">
            {progress.rows.map((row, index) => <button key={`${row.product_id || row.gtin}:${index}`} type="button"
              aria-pressed={positionFilter === index} className={`tsd-position tsd-progress--${progressState(row.addedTotal, row.expected)}`} onClick={() => selectPosition(index)}>
              <strong>{row.product_name}</strong>
              <span>{row.unmarked ? 'Штрихкод' : 'GTIN'}: {row.gtin}</span>
              <b>{row.addedTotal} / {row.expected} шт. · {progressLabels[progressState(row.addedTotal, row.expected)]}{targetProductId === row.product_id ? ' · Выбрана для сканирования' : ''}</b>
            </button>)}
          </div>
          {typeof positionFilter === 'number' && progress.rows[positionFilter]?.product_id && <button type="button" className="tsd-button tsd-button--primary"
            disabled={scan.isPending || undo.isPending || remove.isPending || complete.isPending || Boolean(code.trim())}
            onClick={() => {
              setTargetProductId(progress.rows[positionFilter].product_id!)
              setSelectedScanId(null)
              setMessage(null)
            }}>Сканировать в выбранную позицию</button>}
          <div className="tsd-input-actions">
            <button type="button" className="tsd-button" aria-pressed={positionFilter === 'all'} onClick={() => selectPosition('all')}>Все сканы</button>
            <button type="button" className="tsd-button" aria-pressed={positionFilter === 'other'} onClick={() => selectPosition('other')}>Вне плана</button>
          </div>
          <h3>Сканы: {filteredScans.length}</h3>
          {filteredScans.length === 0 && <p className="hint">По выбранной позиции ещё нет сканов.</p>}
          <div className="tsd-mark-list">
            {filteredScans.slice(0, visibleScans).map((item) => <button type="button" key={item.id}
              className={`tsd-mark tsd-progress--${!rowForScan(item) || ['invalid', 'used_in_other_doc', 'unknown_product', 'overflow'].includes(item.status) ? 'overflow' : ['scanned', 'valid'].includes(item.status) ? 'done' : 'partial'}`} aria-pressed={selectedScanId === item.id} disabled={remove.isPending}
              onClick={() => setSelectedScanId((previous) => previous === item.id ? null : item.id)}>
              <strong>{item.product_name || rowForScan(item)?.product_name || 'Товар вне плана'}</strong>
              <span>GTIN: {effectiveGtinKey(item) || 'не распознан'}{item.box_quantity ? ` · ${item.box_quantity} шт.` : ''}</span>
              <span>{scanPackageLabel(item)}</span>
              <code>{item.code}</code>
              <span>{item.error_message || (['scanned', 'valid', 'overflow'].includes(item.status) ? 'Добавлен в сборку' : 'Не засчитан в сборку')}</span>
            </button>)}
          </div>
          {filteredScans.length > visibleScans && <button type="button" className="tsd-button" onClick={() => setVisibleScans((count) => count + 50)}>Показать ещё 50</button>}
          {selectedScan && <div className="tsd-delete-selection">
            <strong>Выбранный скан</strong><code>{selectedScan.code}</code>
            <p>{scanPackageLabel(selectedScan)}</p>
            {scanUnits(selectedScan) > 1 && <p>Удаляется вся упаковка: {scanUnits(selectedScan)} шт.</p>}
            {!selectedScan.is_box && !selectedScan.is_barcode && scanPackageType(selectedScan) !== 'UNIT' && <>
              <button type="button" className="tsd-button" disabled={!online || scannerBlocked || selectedScan.status === 'pending' || doc.status !== 'draft'}
                onClick={() => packMode.mutate(selectedScan)}>
                {selectedScan.status === 'pending' ? 'Получаем состав…' : selectedScan.keep_aggregate || !selectedScan.child_codes?.length ? 'Раскрыть блок на вложенные марки' : 'Сохранить блок целиком'}
              </button>
              {selectedScan.child_codes?.length ? <details><summary>Марки внутри ({selectedScan.child_codes.length})</summary>
                {selectedScan.child_codes.map((child) => <code key={child}>{child}</code>)}</details> : null}
            </>}
            <button type="button" className="tsd-button tsd-button--danger"
              disabled={!online || doc.status !== 'draft' || remove.isPending || scan.isPending || undo.isPending || complete.isPending}
              onClick={() => remove.mutate(selectedScan.id)}>{remove.isPending ? 'Удаляем…' : 'Удалить выбранную марку'}</button>
          </div>}
        </>}
      </details>
      {lastCode && <details className="tsd-code-debug"><summary>Показать последний код</summary><code>{lastCode}</code></details>}
      <footer className="tsd-actions">
        <button type="button" className="tsd-button tsd-button--danger" disabled={!online || remove.isPending || undo.isPending || scan.isPending || complete.isPending || !last} onClick={() => undo.mutate()}>
          Отменить свой скан
        </button>
        <button type="button" className="tsd-button tsd-button--primary" disabled={!online || scannerBlocked || Boolean(code.trim())} onClick={() => complete.mutate()}>
          Завершить сборку
        </button>
      </footer>
    </main>
  )
}

export function TsdPage() {
  const qc = useQueryClient()
  const [authorized, setAuthorized] = useState(() => Boolean(localStorage.getItem('tsd_access_token')) && !new URLSearchParams(window.location.search).has('pair'))
  const [requestedId, setRequestedId] = useState(() => new URLSearchParams(window.location.search).get('document'))
  const requestedOnce = useRef<string | null>(null)
  const [activeId, setActiveId] = useState(() => localStorage.getItem('tsd_document_id'))
  const [document, setDocument] = useState<TsdDocumentDetail | null>(null)
  const restored = useQuery({
    queryKey: ['tsd-document', activeId],
    queryFn: () => tsdApi.getDocument(activeId!).then((r) => r.data),
    enabled: authorized && Boolean(activeId) && !document && !requestedId,
  })
  const open = (doc: TsdDocumentDetail) => {
    localStorage.setItem('tsd_document_id', doc.id)
    setActiveId(doc.id)
    setDocument(doc)
  }
  const back = () => {
    localStorage.removeItem('tsd_document_id')
    setActiveId(null)
    setDocument(null)
    setRequestedId(null)
    window.history.replaceState({}, '', '/tsd')
  }
  const fromLink = useMutation({
    networkMode: 'always',
    mutationFn: (id: string) => tsdApi.selectDocument(id).then((r) => r.data),
    onSuccess: (doc) => {
      open(doc)
      setRequestedId(null)
      window.history.replaceState({}, '', '/tsd')
    },
  })
  useEffect(() => {
    if (authorized && requestedId && requestedOnce.current !== requestedId) {
      requestedOnce.current = requestedId
      fromLink.mutate(requestedId)
    }
  }, [authorized, requestedId])
  const ready = () => {
    qc.removeQueries({ predicate: (query) => String(query.queryKey[0]).startsWith('tsd-') })
    localStorage.removeItem('tsd_document_id')
    setActiveId(null)
    setDocument(null)
    setAuthorized(true)
  }
  const current = document || (!requestedId ? restored.data : null)
  let content
  if (!authorized) content = <TsdLogin onReady={ready} />
  else if (requestedId || (activeId && !current)) content = <main className="tsd-shell tsd-login">
    <h1>Сборка заказа</h1>
    <p>{fromLink.error ? apiMessage(fromLink.error) : restored.error ? apiMessage(restored.error) : requestedId ? 'Открываем отгрузку…' : 'Восстанавливаем документ…'}</p>
    {requestedId && fromLink.error && <button type="button" className="tsd-button" onClick={() => fromLink.mutate(requestedId)}>Повторить</button>}
    <button type="button" className="tsd-button" disabled={fromLink.isPending} onClick={back}>К списку заказов</button>
  </main>
  else if (current) content = <TsdPicking key={current.id} initial={current} onBack={back} />
  else content = <TsdOrderList onOpen={open} />
  return <><TsdPwaControls />{content}</>
}
