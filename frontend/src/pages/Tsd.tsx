import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { orderSearchPage, useOrderSearch } from '../hooks/useOrderSearch'
import axios from 'axios'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { tsdApi, tsdModeLabels, type TsdMode, type TsdDocumentDetail, type TsdDocumentItem, type TsdOrderShipments } from '../api/client'
import { PhysicalCounts } from '../components/PhysicalCounts'
import { CustomerOrderFilterSelect } from '../components/CustomerOrderFilterSelect'
import { OrderStatusBadge } from '../components/OrderStatusBadge'
import { buildProgress, effectiveGtinKey, findProgressRowForScan, scanUnits, progressAfterScan } from '../store/scanStore'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { sortShipmentProgress } from '../lib/sortShipmentProgress'
import { isProblemScan, numberedScans, problemPositionRows } from '../lib/shipmentProblems'
import { needsVerificationReview, verificationStamp, verificationResultsArrived, verificationReview } from '../lib/tsdVerificationReview'
import { verificationLabel, ownerCheckLabel } from '../lib/scanVerification'
import { TsdPwaControls, TsdConnection, useTsdOnline } from '../components/TsdPwaControls'
import { useTsdSound } from '../hooks/useTsdSound'
import { TsdScanInput } from '../components/TsdScanInput'
import { useDocumentLive, type DocumentEvent } from '../hooks/useDocumentLive'
import { applyScanEvent } from '../lib/scanEvents'
import { parseTsdDocumentCode } from '../lib/tsdLinks'
import { TsdSwipeMark } from '../components/TsdSwipeMark'
import { packActionLabel, scanPackageLabel, scanPackageType, shouldUnpack } from '../lib/scanPackaging'
import { ShipmentCorrections } from '../components/ShipmentCorrections'
import { ResendShipmentMarks } from '../components/ResendShipmentMarks'
import { saveTsdTokens } from '../lib/tsdAuth'

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
      saveTsdTokens(data)
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
  const [correctionsOpen, setCorrectionsOpen] = useState(false)
  const online = useTsdOnline()
  const [search, setSearch] = useState('')
  const [filterId, setFilterId] = useState(() => localStorage.getItem('tsd_order_filter_id') || '')
  const filters = useQuery({ queryKey: ['tsd-order-filters'], queryFn: () => tsdApi.orderFilters().then(r => r.data), refetchInterval: 30_000 })
  const activeFilterId = filters.data?.some(f => f.id === filterId) ? filterId : ''
  const deferredSearch = useOrderSearch(search)
  const [tab, setTab] = useState<'available' | 'work'>('available')
  const [qrMode, setQrMode] = useState(false)
  const [qrValue, setQrValue] = useState('')
  const [openError, setOpenError] = useState<string | null>(null)
  const [orderChoice, setOrderChoice] = useState<TsdOrderShipments | null>(null)
  const qrRef = useRef<HTMLInputElement>(null)
  const { data: me } = useQuery({ queryKey: ['tsd-me'], queryFn: () => tsdApi.me().then((r) => r.data) })
  const { data: orderPages, isLoading, isFetching, error: listError, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ['tsd-orders', deferredSearch, activeFilterId], enabled: !filters.isLoading,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => tsdApi.orders(deferredSearch.trim(), pageParam, activeFilterId).then(orderSearchPage),
    getNextPageParam: last => last.nextCursor,
  })
  const orderRows = orderPages?.pages.flatMap(page => page.rows) || []
  useEffect(() => {
    if (online && orderRows.length === 0 && hasNextPage && !isFetching && !listError) void fetchNextPage()
  }, [online, orderRows.length, hasNextPage, isFetching, listError, fetchNextPage])
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
  const filtered = orderRows.filter((item) => tab === 'work'
    ? (item.active_on_this_device ?? item.in_work) : !(item.active_on_this_device ?? item.in_work))
  const submitQr = (event: FormEvent) => {
    event.preventDefault()
    const id = documentCode(qrValue)
    if (!id) {
      setOpenError('Это не QR отгрузки Скандаты')
      return
    }
    if (online && !select.isPending && !chooseOrder.isPending) select.mutate({ id })
  }
  if (correctionsOpen) return <ShipmentCorrections terminal onClose={() => setCorrectionsOpen(false)} />
  return (
    <main className="tsd-shell">
      <header className="tsd-header">
        <div>
          <h1>{orderChoice ? `Заказ ${orderChoice.order_name}` : 'Заказы покупателей'}</h1>
          <p>{me?.workplace_name || 'Рабочее место'} · {me?.organization_name || 'Юрлицо'}</p>
        </div>
        <TsdConnection />
      </header>
      <button type="button" className="tsd-button" onClick={() => setCorrectionsOpen(true)}>Исправить отгрузку</button>
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
            <p>{hasNextPage && orderRows.length === 0 ? 'Ищем в более ранних заказах…' : tab === 'work' ? 'На этом ТСД нет сборок в работе на этой странице' : 'Нет доступных заказов на этой странице'}</p>
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
  const [hasCode, setHasCode] = useState(false)
  const [inputResetVersion, setInputResetVersion] = useState(0)
  const [lastCode, setLastCode] = useState('')
  const [lastScannedId, setLastScannedId] = useState<string | null>(null)
  const [pickingView, setPickingView] = useState<'card' | 'list'>(() =>
    localStorage.getItem('tsd_shipment_view') === 'list' ? 'list' : 'card')
  const [scanNavigation, setScanNavigation] = useState(0)
  const listView = pickingView === 'list'
  const [reviewing, setReviewing] = useState(!initial.collection_started)
  const [positionFilter, setPositionFilter] = useState<number | 'all' | 'other'>('all')
  const [selectedScanId, setSelectedScanId] = useState<string | null>(null)
  const [swipeScanId, setSwipeScanId] = useState<string | null>(null)
  const [visibleScans, setVisibleScans] = useState(50)
  const [onlyProblemPositions, setOnlyProblemPositions] = useState(false)
  const [onlyProblemMarks, setOnlyProblemMarks] = useState(false)
  const [checkingMarks, setCheckingMarks] = useState(false)
  const [verificationResult, setVerificationResult] = useState<ReturnType<typeof verificationReview> | null>(null)
  const verificationRunRef = useRef<{ baseline: Map<string, string>; accepted: boolean; finishing: boolean } | null>(null)
  const marksReviewRef = useRef<HTMLDivElement>(null)
  const [reviewScroll, setReviewScroll] = useState(0)
  useEffect(() => () => { verificationRunRef.current = null }, [])
  const [repeatedId, setRepeatedId] = useState<string | null>(null)
  const [targetProductId, setTargetProductId] = useState<string | null>(null)
  const submitting = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const reviewRef = useRef<HTMLDetailsElement>(null)
  const scannerDockRef = useRef<HTMLElement>(null)
  const positionRefs = useRef<(HTMLButtonElement | null)[]>([])
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
    refetchInterval: (query) => !query.state.data?.collection_started ? 3000
      : checkingMarks || query.state.data?.scans.some((item) => item.status === 'pending') ? 1500 : false,
  })
  const progress = useMemo(() => sortShipmentProgress(buildProgress(doc.plan, doc.scans)), [doc.plan, doc.scans])
  const hasGroups = progress.rows.some(row => row.folder_name)
  const last = doc.scans[0]
  const rowForScan = (value: typeof last) => findProgressRowForScan(value, progress.rows)
  const lastScanned = doc.scans.find((item) => item.id === lastScannedId) || last
  const lastScannedPosition = lastScanned && rowForScan(lastScanned)
  const lastScannedIndex = lastScannedPosition ? progress.rows.indexOf(lastScannedPosition) : -1
  useEffect(() => {
    if (message?.text !== 'Определяем товар и состав упаковки…' || !lastScannedId
        || !lastScanned || lastScanned.status === 'pending') return
    const outsidePlan = progress.hasPlan && !lastScannedPosition
    const over = lastScannedPosition && lastScannedPosition.expected > 0
      && lastScannedPosition.addedTotal > lastScannedPosition.expected
    setMessage(lastScanned.error_message ? { kind: 'error', text: lastScanned.error_message }
      : outsidePlan ? { kind: 'error', text: `GTIN ${effectiveGtinKey(lastScanned) || 'не определён'} отсутствует в плане отгрузки` }
      : over ? { kind: 'error', text: `Упаковка добавлена сверх плана: ${lastScannedPosition.product_name}` }
      : { kind: 'ok', text: scanPackageLabel(lastScanned) })
  }, [message?.text, lastScannedId, lastScanned, lastScannedPosition, progress.hasPlan])
  useEffect(() => {
    if (!listView || !scanNavigation || lastScannedIndex < 0) return
    setPositionFilter(lastScannedIndex)
    setSelectedScanId(null)
    setSwipeScanId(null)
    setVisibleScans(50)
    const element = positionRefs.current[lastScannedIndex]
    if (element) {
      element.style.scrollMarginTop = `${(scannerDockRef.current?.offsetHeight || 160) + 8}px`
      element.scrollIntoView({ block: 'start', behavior: 'instant' })
    }
  }, [listView, scanNavigation, lastScannedIndex])
  const changePickingView = (view: 'card' | 'list') => {
    setPickingView(view)
    localStorage.setItem('tsd_shipment_view', view)
    inputRef.current?.focus({ preventScroll: true })
  }
  const target = progress.rows.find((row) => row.product_id === targetProductId && targetProductId)
  const selectedPosition = typeof positionFilter === 'number' ? progress.rows[positionFilter] : undefined
  const current = target || (last && rowForScan(last)) || progress.rows.find((row) => row.addedTotal < row.expected) || progress.rows[0]
  const selectedScan = doc.scans.find((item) => item.id === selectedScanId)
  const activePack = selectedScan || lastScanned
  const canChangePack = activePack && !activePack.is_barcode
    && scanPackageType(activePack) !== 'UNIT'
  const showReview = listView || reviewing
  const visiblePositions = useMemo(() => {
    if (!showReview) return []
    const problems = onlyProblemPositions
      ? new Set(problemPositionRows(progress, doc.scans, null, repeatedId)) : null
    return progress.rows.map((row, index) => ({ row, index }))
      .filter(({ row }) => !problems || problems.has(row))
  }, [showReview, onlyProblemPositions, progress, doc.scans, repeatedId])
  const orderedScans = useMemo(() => showReview ? numberedScans(doc.scans) : [], [showReview, doc.scans])
  const { problemCount, filteredScans } = useMemo(() => {
    let problemCount = 0
    const filteredScans: typeof orderedScans = []
    for (const item of orderedScans) {
      const row = positionFilter === 'all' ? undefined : findProgressRowForScan(item.scan, progress.rows)
      if (positionFilter !== 'all'
          && (positionFilter === 'other' ? Boolean(row) : row !== progress.rows[positionFilter])) continue
      const problem = isProblemScan(item.scan, null, repeatedId, progress.hasPlan ? progress.rows : undefined)
        || Boolean(verificationResult && needsVerificationReview(item.scan, progress.hasPlan ? progress.rows : undefined))
      if (problem) problemCount++
      if (!onlyProblemMarks || problem) filteredScans.push(item)
    }
    return { problemCount, filteredScans }
  }, [orderedScans, positionFilter, progress, repeatedId, verificationResult, onlyProblemMarks])
  const selectPosition = (value: typeof positionFilter) => {
    setPositionFilter(value)
    setSelectedScanId(null)
    setSwipeScanId(null)
    setVisibleScans(50)
  }
  const percent = progress.total.expected > 0
    ? Math.min(100, Math.round(progress.total.addedTotal * 100 / progress.total.expected))
    : 0
  const refresh = () => qc.invalidateQueries({ queryKey: ['tsd-document', initial.id] })
  const finishVerification = async (failed = 0) => {
    const run = verificationRunRef.current
    if (!run || run.finishing) return
    run.finishing = true
    try {
      await qc.invalidateQueries({ queryKey: ['tsd-document', initial.id] }, { throwOnError: true })
      if (verificationRunRef.current !== run) return
      const latest = qc.getQueryData<TsdDocumentDetail>(['tsd-document', initial.id]) || doc
      const result = verificationReview(latest.scans, latest.plan, failed)
      verificationRunRef.current = null
      setCheckingMarks(false)
      setVerificationResult(result)
      setMessage({ kind: result.hasIssues ? 'error' : 'ok', text: result.text })
      setReviewing(true)
      setPositionFilter('all')
      setSelectedScanId(null)
      setSwipeScanId(null)
      setVisibleScans(50)
      setOnlyProblemMarks(result.hasIssues)
      setReviewScroll(value => value + 1)
      sound.play(result.hasIssues ? 'error' : 'ok')
      window.navigator.vibrate?.(result.hasIssues ? [100, 60, 100] : 60)
    } catch {
      run.finishing = false
      setMessage({ kind: 'error', text: 'Не удалось загрузить результаты проверки. Восстанавливаем связь…' })
    }
  }
  const verify = useMutation({
    mutationFn: () => tsdApi.verify(doc.id),
    onMutate: () => {
      const latest = qc.getQueryData<TsdDocumentDetail>(['tsd-document', initial.id]) || doc
      verificationRunRef.current = { baseline: new Map(latest.scans.filter(scan => !scan.is_barcode)
        .map(scan => [scan.id, verificationStamp(scan)])), accepted: false, finishing: false }
      setCheckingMarks(true)
      setVerificationResult(null)
      setMessage({ kind: 'ok', text: 'Проверяем марки в ЧЗ. Дождитесь результата…' })
    },
    onSuccess: () => {
      if (verificationRunRef.current) verificationRunRef.current.accepted = true
      void refresh()
    },
    onError: (error) => {
      verificationRunRef.current = null
      setCheckingMarks(false)
      setMessage({ kind: 'error', text: apiMessage(error) })
      sound.play('error')
    },
  })
  useEffect(() => {
    const run = verificationRunRef.current
    if (checkingMarks && run?.accepted && verificationResultsArrived(run.baseline, doc.scans)) void finishVerification()
  }, [doc.scans, checkingMarks])
  useEffect(() => {
    if (!checkingMarks) return
    const timeout = window.setTimeout(() => {
      verificationRunRef.current = null
      setCheckingMarks(false)
      setMessage({ kind: 'error', text: 'Нет подтверждения завершения проверки. Проверьте связь и повторите проверку марок.' })
    }, 15 * 60 * 1000)
    return () => window.clearTimeout(timeout)
  }, [checkingMarks])
  useEffect(() => {
    if (!reviewScroll) return
    const frame = requestAnimationFrame(() => {
      const element = marksReviewRef.current
      if (!element) return
      element.style.scrollMarginTop = `${(scannerDockRef.current?.offsetHeight || 160) + 8}px`
      element.scrollIntoView({ block: 'start', behavior: 'instant' })
    })
    return () => cancelAnimationFrame(frame)
  }, [reviewScroll])
  useDocumentLive(initial.id, true, (event) => {
    if (event.type === 'verify_done') {
      if (verificationRunRef.current) void finishVerification(Number(event.failed) || 0)
      else void refresh()
      return
    }
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
    onSuccess: async (result) => {
      // Ignore any older recovery snapshot without reverting live updates that
      // arrived while it was loading. The acknowledged scan is authoritative.
      await qc.cancelQueries({ queryKey: ['tsd-document', initial.id] }, { revert: false })
      setVerificationResult(null)
      setLastScannedId(result.id)
      setRepeatedId(result.duplicate ? result.id : null)
      setSelectedScanId(null)
      setScanNavigation(value => value + 1)
      const rejected = ['invalid', 'used_in_other_doc', 'unknown_product'].includes(result.status)
      const latest = qc.getQueryData<TsdDocumentDetail>(['tsd-document', initial.id]) || doc
      const { scans: mergedScans, matched, overPlan } = progressAfterScan(latest.plan, latest.scans, result)
      const resolvingPackage = result.status === 'pending' && ['packaging_pending', 'sscc_pending'].includes(result.verification?.source || '')
      const errorFeedback = Boolean(result.duplicate || rejected || (!matched && !resolvingPackage) || overPlan)
      setMessage(result.duplicate
        ? { kind: 'error', text: 'Этот код уже отсканирован' }
        : rejected ? { kind: 'error', text: result.error_message || 'Не удалось распознать марку' }
        : resolvingPackage ? { kind: 'ok', text: 'Определяем товар и состав упаковки…' }
        : !matched ? { kind: 'error', text: `Скан записан, но GTIN ${effectiveGtinKey(result) || 'не определён'} отсутствует в плане отгрузки` }
        : overPlan ? { kind: 'error', text: `Марка добавлена сверх плана: ${matched.product_name}` }
        : { kind: 'ok', text: result.product_name || matched.product_name || 'Скан принят' })
      qc.setQueryData<TsdDocumentDetail>(['tsd-document', initial.id], { ...latest, scans: mergedScans })
      setInputResetVersion(value => value + 1)
      sound.play(result.status === 'used_in_other_doc' ? 'used_in_other_doc' : errorFeedback ? 'error' : 'ok')
      window.navigator.vibrate?.(errorFeedback ? [80, 60, 80] : 60)
      inputRef.current?.focus({ preventScroll: true })
    },
    onError: (error) => {
      setMessage({ kind: 'error', text: apiMessage(error) })
      setInputResetVersion(value => value + 1)
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
      setSwipeScanId(null)
      setMessage({ kind: 'ok', text: 'Выбранная марка удалена из сборки' })
      refresh()
    },
    onError: (error) => { setMessage({ kind: 'error', text: apiMessage(error) }); sound.play('error') },
  })
  const startCollection = useMutation({
    networkMode: 'always',
    mutationFn: () => tsdApi.startCollection(doc.id),
    onSuccess: () => {
      qc.setQueryData<TsdDocumentDetail>(['tsd-document', initial.id], (previous) => previous
        ? { ...previous, collection_started: true } : previous)
      setInputResetVersion(value => value + 1)
      setReviewing(false)
      setMessage(null)
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
  const release = useMutation({
    networkMode: 'always',
    mutationFn: () => tsdApi.release(doc.id),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['tsd-orders'] }); onBack() },
    onError: (error) => { setMessage({ kind: 'error', text: apiMessage(error) }); sound.play('error') },
  })
  const packMode = useMutation({
    networkMode: 'always',
    mutationFn: (item: typeof last) => tsdApi.packMode(doc.id, item.id, shouldUnpack(item)),
    onSuccess: ({ data }) => {
      qc.setQueryData<TsdDocumentDetail>(['tsd-document', initial.id], previous => previous
        ? { ...previous, scans: previous.scans.map(item => item.id === data.id ? data : item) } : previous)
      setMessage({ kind: 'ok', text: data.status === 'pending' ? 'Получаем состав упаковки из Честного Знака…' : scanPackageLabel(data) })
      refresh()
    },
    onError: (error) => { setMessage({ kind: 'error', text: apiMessage(error) }); sound.play('error') },
  })
  useEffect(() => {
    // Android scanners may inject through the IME and need an editable, focused input.
    // Restore focus after React applies mode changes or removes readOnly after a request.
    if (doc.collection_started && !scan.isPending && !undo.isPending && !complete.isPending && !remove.isPending && !packMode.isPending && !release.isPending) {
      inputRef.current?.focus({ preventScroll: true })
    }
  }, [doc.collection_started, targetProductId, scan.isPending, undo.isPending, complete.isPending, remove.isPending, packMode.isPending, release.isPending])
  const scannerBlocked = doc.status !== 'draft' || !doc.collection_started || startCollection.isPending || scan.isPending || undo.isPending || complete.isPending || remove.isPending || packMode.isPending || release.isPending
  const acceptCode = (code: string) => {
    const normalized = normalizeScannerInput(code).trim()
    if (!normalized || submitting.current || scannerBlocked) return
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
  return (
    <main className="tsd-shell tsd-picking">
      <section ref={scannerDockRef} className="tsd-scanner-dock" aria-label="Сканер маркировки">
        <TsdScanInput inputRef={inputRef} blocked={scannerBlocked} online={online}
          collectionStarted={Boolean(doc.collection_started)} pending={scan.isPending} messageKind={message?.kind}
          resetVersion={inputResetVersion} onHasCodeChange={setHasCode} onScan={acceptCode} />
        <div className="tsd-dock-meta">
          <button type="button" className="tsd-button" disabled={!online || checkingMarks || verify.isPending || doc.status !== 'draft' || !doc.scans.some(item => !item.is_barcode)} aria-busy={checkingMarks} onClick={() => verify.mutate()}>{checkingMarks ? 'Проверяем марки в ЧЗ…' : 'Проверить все марки в ЧЗ'}</button>
          <span title={target?.product_name}>{targetProductId ? `Марки → ${target?.product_name || 'Позиция недоступна'}` : 'Авто по GTIN'}</span>
          {(selectedPosition?.product_id || targetProductId) && <button type="button" className="tsd-button" aria-pressed={!targetProductId} disabled={scannerBlocked || hasCode} onClick={() => setTargetProductId(null)}>Авто</button>}
          {selectedPosition?.product_id && <button type="button"
            className={`tsd-button${targetProductId === selectedPosition.product_id ? ' tsd-button--primary' : ''}`}
            aria-pressed={targetProductId === selectedPosition.product_id}
            title={`Сканировать в позицию: ${selectedPosition.product_name}`}
            disabled={scannerBlocked || hasCode}
            onClick={() => {
              setTargetProductId(selectedPosition.product_id!)
              setSelectedScanId(null)
              setMessage(null)
            }}>Режим добавления</button>}
          <button type="button" data-tsd-sound className="tsd-button" aria-label={sound.active ? 'Отключить звук' : 'Включить звук'} aria-pressed={sound.active} onClick={() => void sound.toggle()}>{sound.active ? 'Звук вкл.' : 'Звук выкл.'}</button>
        </div>
        {canChangePack && <div className="tsd-pack-control" aria-label="Обработка упаковки">
          <span>{selectedScan ? 'Выбранная упаковка' : 'Последняя упаковка'} · {scanPackageLabel(activePack)}</span>
          <button type="button" className="tsd-button tsd-button--primary"
            disabled={!online || scannerBlocked || hasCode || activePack.status === 'pending'
              || ['invalid', 'used_in_other_doc'].includes(activePack.status) || doc.status !== 'draft'}
            onClick={() => packMode.mutate(activePack)}>
            {activePack.status === 'pending' ? 'Получаем состав…' : packActionLabel(activePack)}
          </button>
          {activePack.error_message && <span role="alert">{activePack.error_message}</span>}
        </div>}
        {message && <div role="status" aria-live="polite" className={`tsd-dock-message tsd-dock-message--${message.kind}`}>{message.text}</div>}
      </section>
      <header className="tsd-header tsd-header--picking">
        <button type="button" className="tsd-back" onClick={onBack} aria-label="Назад">‹</button>
        <div><h1>Сборка заказа</h1><p>{doc.customer_order_name ? `Заказ ${doc.customer_order_name} · Отгрузка ${doc.name}` : doc.name}</p></div>
        <TsdConnection />
      </header>
      {doc.active_on_other_device ? <div className="tsd-alert tsd-alert--warn">Отгрузка также открыта на другом ТСД</div> : null}
      {doc.can_resend_marks && <ResendShipmentMarks key={doc.id} documentId={doc.id} terminal disabled={!online}
        onStateChange={() => { void qc.invalidateQueries({ queryKey: ['tsd-document', doc.id] }) }} />}
      {doc.error_message && <div role="alert" className="tsd-alert tsd-alert--error">{doc.error_message}</div>}
      {!doc.collection_started && <section className="tsd-current" style={{ padding: 18 }}>
        <p>Проверьте план отгрузки и начните сборку.</p>
        <button type="button" className="tsd-button tsd-button--primary" disabled={!online || startCollection.isPending}
          onClick={() => startCollection.mutate()}>{startCollection.isPending ? 'Начинаем сборку…' : 'Начать сборку'}</button>
      </section>}
      <section className="tsd-total">
        <span>Собрано</span>
        <strong>{progress.total.addedTotal}<small>/{progress.total.expected || '—'}</small></strong>
        <b>{percent}%</b>
        <div><i style={{ width: `${percent}%` }} /></div>
      </section>
      <div className="tsd-input-actions" role="group" aria-label="Режим экрана отгрузки">
        <button type="button" className={`tsd-button${!listView ? ' tsd-button--primary' : ''}`} aria-pressed={!listView} onClick={() => changePickingView('card')}>Карточка товара</button>
        <button type="button" className={`tsd-button${listView ? ' tsd-button--primary' : ''}`} aria-pressed={listView} onClick={() => changePickingView('list')}>Список товаров</button>
      </div>
      {!listView && <section className={`tsd-current tsd-progress--${progressState(current?.addedTotal || 0, current?.expected || 0)}${current && current.expected > 0 && current.addedTotal < current.expected ? ' assembly-incomplete' : ''}`}>
        <div><span>{target ? 'Выбранный товар' : 'Текущий товар'}</span><small>{progress.rows.indexOf(current) + 1} из {progress.rows.length}</small></div>
        <h2>{current?.product_name || 'Сканируйте товар'}</h2>
        {last && <p className="hint">GTIN последнего скана: {effectiveGtinKey(last) || 'не распознан'}</p>}
        <div className="tsd-counts">
          <p><span>Ожидалось</span><b>{current?.expected ?? '—'} <small>шт.</small></b></p>
          <p><span>Собрано</span><b>{current?.addedTotal ?? 0} <small>шт.</small></b></p>
        </div>
      </section>}
      <details ref={reviewRef} className="tsd-order-review" open={listView || reviewing} onToggle={(event) => { if (!listView) setReviewing(event.currentTarget.open) }}>
        <summary onClick={event => { if (listView) event.preventDefault() }}>Позиции заказа ({progress.rows.length}) и сканы ({doc.scans.length})</summary>
          <p className="hint">Выберите позицию для привязки следующих марок. Смахните марку влево, чтобы показать корзину для удаления.</p>
        {(listView || reviewing) && <>
          <div className="tsd-position-list">
            <label className="shipment-problem-filter" title="Недобор, превышение плана и позиции с проблемными марками">
              <input type="checkbox" checked={onlyProblemPositions} onChange={e => setOnlyProblemPositions(e.target.checked)} />
              Только проблемные позиции
            </label>
            {onlyProblemPositions && visiblePositions.length === 0 && <p className="hint">В плане проблемных позиций нет. Сканы вне плана доступны ниже.</p>}
            {visiblePositions.map(({ row, index }, visibleIndex) => <Fragment key={`${row.product_id || row.gtin}:${index}`}>
              {hasGroups && (visibleIndex === 0 || row.folder_name !== visiblePositions[visibleIndex - 1].row.folder_name) &&
                <div className="shipment-group-heading">{row.folder_name || 'Без группы'}</div>}
              <button type="button"
              ref={element => { positionRefs.current[index] = element }}
              aria-pressed={positionFilter === index} className={`tsd-position tsd-progress--${progressState(row.addedTotal, row.expected)}${row.expected > 0 && row.addedTotal < row.expected ? ' assembly-incomplete' : ''}${row === lastScannedPosition ? ' tsd-recent-scan' : ''}`} onClick={() => selectPosition(index)}>
              {row === lastScannedPosition && <span className="tsd-recent-scan__label">Последняя отсканированная позиция</span>}
              <strong>{row.product_name}</strong>
              <span>{row.unmarked ? 'Штрихкод' : 'GTIN'}: {row.gtin}</span>
              <b>{row.addedTotal} / {row.expected} шт. · {progressLabels[progressState(row.addedTotal, row.expected)]}{targetProductId === row.product_id ? ' · Выбрана для сканирования' : ''}</b>
            </button></Fragment>)}
          </div>
          <div className="tsd-input-actions">
            <button type="button" className="tsd-button" aria-pressed={positionFilter === 'all'} onClick={() => selectPosition('all')}>Все сканы</button>
            <button type="button" className="tsd-button" aria-pressed={positionFilter === 'other'} onClick={() => selectPosition('other')}>Вне плана</button>
          </div>
          <div ref={marksReviewRef} aria-label="Результаты проверки марок">
          {verificationResult && <div className={`tsd-alert ${verificationResult.hasIssues ? 'tsd-alert--error' : 'tsd-alert--ok'}`}
            role={verificationResult.hasIssues ? 'alert' : 'status'}>
            <strong>{verificationResult.text}</strong>
            {verificationResult.hasIssues && <p>{onlyProblemMarks ? 'Показаны только проблемные марки. Снимите фильтр, чтобы увидеть все сканы.' : 'Показаны все сканы.'}</p>}
          </div>}
          <h3>Сканы: {filteredScans.length}</h3>
          <label className="shipment-problem-filter">
            <input type="checkbox" checked={onlyProblemMarks} onChange={e => { setOnlyProblemMarks(e.target.checked); setVisibleScans(50) }} />
            Только проблемные марки ({problemCount})
          </label>
          {filteredScans.length === 0 && <p className="hint">{onlyProblemMarks ? 'Проблемных марок нет.' : 'По выбранной позиции ещё нет сканов.'}</p>}
          </div>
          <div className="tsd-mark-list">
            {filteredScans.slice(0, visibleScans).map(({ scan: item, number }) => <TsdSwipeMark key={item.id}
              className={`tsd-mark tsd-progress--${item.verification?.ms_error || item.verification?.owner_result === 'mismatch' || (item.status !== 'pending' && !rowForScan(item)) || ['invalid', 'used_in_other_doc', 'unknown_product', 'overflow'].includes(item.status) ? 'overflow' : item.verification?.checked_at && item.status === 'valid' ? 'done' : 'partial'}${item.id === lastScanned?.id ? ' tsd-recent-scan' : ''}`}
              selected={selectedScanId === item.id} open={swipeScanId === item.id} disabled={!online || scannerBlocked || doc.status !== 'draft'}
              onSelect={() => { setSwipeScanId(null); setSelectedScanId(previous => previous === item.id ? null : item.id) }}
              onReveal={open => { setSwipeScanId(open ? item.id : null); if (open) setSelectedScanId(item.id) }}
              deleteLabel={scanUnits(item) > 1 ? `Удалить упаковку: ${scanUnits(item)} шт.` : 'Удалить марку'}
              onDelete={() => remove.mutate(item.id)}>
              {item.id === lastScanned?.id && <span className="tsd-recent-scan__label">Последняя отсканированная марка</span>}
              <strong>№ {number} · {item.product_name || rowForScan(item)?.product_name || 'Товар вне плана'}</strong>
              <span>GTIN: {effectiveGtinKey(item) || 'не распознан'}{item.box_quantity ? ` · ${item.box_quantity} шт.` : ''}</span>
              <span>{scanPackageLabel(item)}</span>
              <code>{item.code}</code>
              <span>{item.error_message || verificationLabel(item)}</span>
              {!item.is_barcode && item.verification?.owner_result !== 'match' && <span>{item.verification?.owner_reason || ownerCheckLabel(item, null)}</span>}
              {item.verification?.checked_at && <small>Проверка ЧЗ: {new Date(item.verification.checked_at).toLocaleString('ru-RU')}</small>}
            </TsdSwipeMark>)}
          </div>
          {filteredScans.length > visibleScans && <button type="button" className="tsd-button" onClick={() => setVisibleScans((count) => count + 50)}>Показать ещё 50</button>}
          {selectedScan && <div className="tsd-delete-selection">
            <strong>Выбранный скан</strong><code>{selectedScan.code}</code>
            <p>{scanPackageLabel(selectedScan)}</p>
            {scanUnits(selectedScan) > 1 && <p>Удаляется вся упаковка: {scanUnits(selectedScan)} шт.</p>}
            {!selectedScan.is_barcode && scanPackageType(selectedScan) !== 'UNIT' && <>
              {selectedScan.child_codes?.length ? <details><summary>Марки внутри ({selectedScan.child_codes.length})</summary>
                {selectedScan.child_codes.map((child) => <code key={child}>{child}</code>)}</details> : null}
            </>}
            <button type="button" className="tsd-button tsd-button--danger"
              disabled={!online || doc.status !== 'draft' || scannerBlocked}
              onClick={() => remove.mutate(selectedScan.id)}>{remove.isPending ? 'Удаляем…' : 'Удалить выбранную марку'}</button>
          </div>}
        </>}
      </details>
      {lastCode && <details className="tsd-code-debug"><summary>Показать последний код</summary><code>{lastCode}</code></details>}
      <footer className="tsd-actions">
        <button type="button" className="tsd-button tsd-button--danger" disabled={!online || scannerBlocked || !last} onClick={() => undo.mutate()}>
          Отменить свой скан
        </button>
        <button type="button" className="tsd-button tsd-button--primary" disabled={!online || scannerBlocked || hasCode} onClick={() => complete.mutate()}>
          Завершить сборку
        </button>
      </footer>
      <div className="tsd-release">
        <button type="button" className="tsd-button" disabled={!online || scannerBlocked || hasCode}
          onClick={() => release.mutate()}>{release.isPending ? 'Убираем с этого ТСД…' : 'Убрать сборку из работы на этом ТСД'}</button>
        <p className="hint">Марки сохранятся. Другие ТСД смогут продолжить сборку, а вы сможете открыть её снова.</p>
      </div>
    </main>
  )
}

export function TsdPage() {
  const qc = useQueryClient()
  const [authorized, setAuthorized] = useState(() => Boolean(localStorage.getItem('tsd_access_token') || localStorage.getItem('tsd_refresh_token')) && !new URLSearchParams(window.location.search).has('pair'))
  const [requestedId, setRequestedId] = useState(() => new URLSearchParams(window.location.search).get('document'))
  const [mode, setMode] = useState<TsdMode | null>(() => new URLSearchParams(window.location.search).has('document') || localStorage.getItem('tsd_document_id') ? 'shipment' : null)
  const context = useQuery({ queryKey: ['tsd-mode-context'], queryFn: () => tsdApi.me().then(r => r.data), enabled: authorized, refetchInterval: 10000 })
  const allowed = context.data?.allowed_modes || ['shipment']
  useEffect(() => { if (context.data && mode && !allowed.includes(mode)) { setMode(null); setDocument(null); setActiveId(null); setRequestedId(null); localStorage.removeItem('tsd_document_id') } }, [context.data, mode])
  const requestedOnce = useRef<string | null>(null)
  const [activeId, setActiveId] = useState(() => localStorage.getItem('tsd_document_id'))
  const [document, setDocument] = useState<TsdDocumentDetail | null>(null)
  const restored = useQuery({
    queryKey: ['tsd-document', activeId],
    queryFn: () => tsdApi.getDocument(activeId!).then((r) => r.data),
    enabled: authorized && mode === 'shipment' && Boolean(activeId) && !document && !requestedId && Boolean(context.data) && allowed.includes('shipment'),
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
    if (authorized && context.data && allowed.includes('shipment') && mode === 'shipment' && requestedId && requestedOnce.current !== requestedId) {
      requestedOnce.current = requestedId
      fromLink.mutate(requestedId)
    }
  }, [authorized, requestedId, context.data, mode])
  const ready = () => {
    qc.removeQueries({ predicate: (query) => String(query.queryKey[0]).startsWith('tsd-') })
    localStorage.removeItem('tsd_document_id')
    setActiveId(null)
    setDocument(null)
    setMode(null)
    setAuthorized(true)
  }
  const current = document || (!requestedId ? restored.data : null)
  let content
  if (!authorized) content = <TsdLogin onReady={ready} />
  else if (!context.data) content = <main className="tsd-shell"><p>{context.error ? apiMessage(context.error) : 'Загружаем доступные режимы…'}</p></main>
  else if (!mode || !allowed.includes(mode)) content = <main className="tsd-shell tsd-mode-menu"><header className="tsd-header"><div><h1>Выберите режим</h1><p>{context.data.workplace_name}</p></div><TsdConnection /></header>
    {(Object.keys(tsdModeLabels) as TsdMode[]).map(value => <button className="tsd-order" key={value} disabled={!allowed.includes(value)} onClick={() => setMode(value)}><strong>{tsdModeLabels[value]}</strong><span>{!allowed.includes(value) ? 'Доступ включается в настройках этого ТСД' : value === 'shipment' ? 'Сборка заказов покупателей' : value === 'acceptance' ? 'Сверка позиций XML, загруженного на ПК' : 'Остатки склада и проверка по брендам'}</span></button>)}
    {!allowed.length && <p className="tsd-alert">Доступ к режимам отключён. Обратитесь к администратору.</p>}
  </main>
  else if (mode !== 'shipment') content = <PhysicalCounts key={`${context.data.device_id}:${mode}`} mode={mode} terminal deviceId={context.data.device_id} onBack={() => setMode(null)} />
  else if (requestedId || (activeId && !current)) content = <main className="tsd-shell tsd-login">
    <h1>Сборка заказа</h1>
    <p>{fromLink.error ? apiMessage(fromLink.error) : restored.error ? apiMessage(restored.error) : requestedId ? 'Открываем отгрузку…' : 'Восстанавливаем документ…'}</p>
    {requestedId && fromLink.error && <button type="button" className="tsd-button" onClick={() => fromLink.mutate(requestedId)}>Повторить</button>}
    <button type="button" className="tsd-button" disabled={fromLink.isPending} onClick={back}>К списку заказов</button>
  </main>
  else if (current) content = <TsdPicking key={current.id} initial={current} onBack={back} />
  else content = <><nav className="tsd-mode-nav"><button className="tsd-button" onClick={() => setMode(null)}>‹ К выбору режима</button></nav><TsdOrderList onOpen={open} /></>
  return <><TsdPwaControls />{content}</>
}
