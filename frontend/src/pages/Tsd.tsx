import { useDeferredValue, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import axios from 'axios'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { tsdApi, type TsdDocumentDetail, type TsdDocumentItem } from '../api/client'
import { buildProgress } from '../store/scanStore'
import { normalizeScannerInput } from '../lib/scannerLayout'

function apiMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
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
  const value = raw.trim()
  if (value.startsWith('SKANDATA:DOCUMENT:')) {
    return value.slice('SKANDATA:DOCUMENT:'.length)
  }
  try {
    return new URL(value).searchParams.get('document')
  } catch {
    return null
  }
}

function TsdLogin({ onReady }: { onReady: () => void }) {
  const [code, setCode] = useState(() => new URLSearchParams(window.location.search).get('pair') || '')
  const [name, setName] = useState(() => localStorage.getItem('tsd_device_name') || 'ТСД АТОЛ')
  const inputRef = useRef<HTMLInputElement>(null)
  const exchange = useMutation({
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
    if (code && new URLSearchParams(window.location.search).get('pair')) exchange.mutate()
    // Автообмен нужен только при первом открытии app-link.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (code.trim() && name.trim()) exchange.mutate()
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
        <button type="submit" className="tsd-button tsd-button--primary" disabled={exchange.isPending || !code.trim()}>
          {exchange.isPending ? 'Подключаем…' : 'Подключить устройство'}
        </button>
      </form>
    </main>
  )
}

function ShipmentRow({ item, onOpen }: { item: TsdDocumentItem; onOpen: () => void }) {
  const progress = item.expected > 0 ? Math.min(100, Math.round(item.collected * 100 / item.expected)) : 0
  return (
    <button type="button" className="tsd-shipment-row" onClick={onOpen}>
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

function TsdShipmentList({ onOpen }: { onOpen: (doc: TsdDocumentDetail) => void }) {
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const [tab, setTab] = useState<'available' | 'work'>('available')
  const [qrMode, setQrMode] = useState(false)
  const [qrValue, setQrValue] = useState('')
  const [openError, setOpenError] = useState<string | null>(null)
  const qrRef = useRef<HTMLInputElement>(null)
  const { data: me } = useQuery({ queryKey: ['tsd-me'], queryFn: () => tsdApi.me().then((r) => r.data) })
  const { data: documents = [], isLoading, refetch } = useQuery({
    queryKey: ['tsd-documents', deferredSearch],
    queryFn: () => tsdApi.documents(deferredSearch.trim()).then((r) => r.data),
  })
  const select = useMutation({
    mutationFn: (id: string) => tsdApi.selectDocument(id).then((r) => r.data),
    onSuccess: onOpen,
    onError: (error) => setOpenError(apiMessage(error)),
  })
  useEffect(() => {
    if (qrMode) qrRef.current?.focus()
  }, [qrMode])
  const filtered = documents.filter((item) => tab === 'work' ? item.in_work : !item.in_work)
  const submitQr = (event: FormEvent) => {
    event.preventDefault()
    const id = documentCode(qrValue)
    if (!id) {
      setOpenError('Это не QR отгрузки Скандаты')
      return
    }
    select.mutate(id)
  }
  return (
    <main className="tsd-shell">
      <header className="tsd-header">
        <div>
          <h1>Отгрузки</h1>
          <p>{me?.workplace_name || 'Рабочее место'} · {me?.organization_name || 'Юрлицо'}</p>
        </div>
        <span className="tsd-online"><i />Онлайн</span>
      </header>
      <div className="tsd-list-controls">
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
      {openError ? <div className="tsd-alert tsd-alert--error">{openError}</div> : null}
      <section className="tsd-shipment-list" aria-live="polite">
        {isLoading ? <p className="tsd-empty">Загружаем отгрузки…</p> : null}
        {!isLoading && filtered.length === 0 ? (
          <div className="tsd-empty">
            <p>{tab === 'work' ? 'Нет начатых сборок' : 'Нет доступных отгрузок'}</p>
            <button type="button" className="tsd-button" onClick={() => refetch()}>Обновить</button>
          </div>
        ) : null}
        {filtered.map((item) => (
          <ShipmentRow key={item.moysklad_id} item={item} onOpen={() => select.mutate(item.moysklad_id)} />
        ))}
      </section>
    </main>
  )
}

function TsdPicking({ initial, onBack }: { initial: TsdDocumentDetail; onBack: () => void }) {
  const qc = useQueryClient()
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [code, setCode] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const { data: doc = initial } = useQuery({
    queryKey: ['tsd-document', initial.id],
    queryFn: () => tsdApi.getDocument(initial.id).then((r) => r.data),
    initialData: initial,
    refetchInterval: 10_000,
  })
  const progress = useMemo(() => buildProgress(doc.plan, doc.scans), [doc.plan, doc.scans])
  const current = progress.rows.find((row) => row.addedTotal < row.expected) || progress.rows[0]
  const last = doc.scans[0]
  const percent = progress.total.expected > 0
    ? Math.min(100, Math.round(progress.total.addedTotal * 100 / progress.total.expected))
    : 0
  const refresh = () => qc.invalidateQueries({ queryKey: ['tsd-document', initial.id] })
  const scan = useMutation({
    mutationFn: (value: string) => tsdApi.scan(doc.id, value).then((r) => r.data),
    onSuccess: (result) => {
      setMessage(result.duplicate
        ? { kind: 'error', text: 'Этот код уже отсканирован' }
        : { kind: 'ok', text: result.product_name || 'Последний скан принят' })
      setCode('')
      refresh()
      window.navigator.vibrate?.(result.duplicate ? [80, 60, 80] : 60)
      inputRef.current?.focus()
    },
    onError: (error) => {
      setMessage({ kind: 'error', text: apiMessage(error) })
      setCode('')
      window.navigator.vibrate?.([120, 80, 120])
      inputRef.current?.focus()
    },
  })
  const undo = useMutation({
    mutationFn: () => tsdApi.undoLast(doc.id),
    onSuccess: () => { setMessage(null); refresh() },
    onError: (error) => setMessage({ kind: 'error', text: apiMessage(error) }),
  })
  const complete = useMutation({
    mutationFn: () => tsdApi.complete(doc.id),
    onSuccess: onBack,
    onError: (error) => setMessage({ kind: 'error', text: apiMessage(error) }),
  })
  useEffect(() => { inputRef.current?.focus() }, [])
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const normalized = normalizeScannerInput(code).trim()
    if (normalized && !scan.isPending) scan.mutate(normalized)
  }
  return (
    <main className="tsd-shell tsd-picking">
      <header className="tsd-header tsd-header--picking">
        <button type="button" className="tsd-back" onClick={onBack} aria-label="Назад">‹</button>
        <div><h1>Сборка заказа</h1><p>{doc.name}</p></div>
        <span className="tsd-online"><i />Онлайн</span>
      </header>
      {doc.active_on_other_device ? <div className="tsd-alert tsd-alert--warn">Отгрузка также открыта на другом ТСД</div> : null}
      <section className="tsd-total">
        <span>Собрано</span>
        <strong>{progress.total.addedTotal}<small>/{progress.total.expected || '—'}</small></strong>
        <b>{percent}%</b>
        <div><i style={{ width: `${percent}%` }} /></div>
      </section>
      <section className="tsd-current">
        <div><span>Текущий товар</span><small>{progress.rows.indexOf(current) + 1} из {progress.rows.length}</small></div>
        <h2>{current?.product_name || 'Сканируйте товар'}</h2>
        <div className="tsd-counts">
          <p><span>Ожидалось</span><b>{current?.expected ?? '—'} <small>шт.</small></b></p>
          <p><span>Собрано</span><b>{current?.addedTotal ?? 0} <small>шт.</small></b></p>
        </div>
      </section>
      <form className="tsd-scan-box" onSubmit={submit}>
        <span aria-hidden>▥</span>
        <label htmlFor="tsd-scan-input">Сканируйте штрихкод</label>
        <input id="tsd-scan-input" ref={inputRef} value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" />
      </form>
      {message || last ? (
        <div className={`tsd-last ${message?.kind === 'error' ? 'tsd-last--error' : ''}`}>
          <b>{message?.kind === 'error' ? 'Ошибка сканирования' : 'Последний скан принят'}</b>
          <span>{message?.text || last?.product_name || last?.code}</span>
        </div>
      ) : null}
      <footer className="tsd-actions">
        <button type="button" className="tsd-button tsd-button--danger" disabled={undo.isPending || !last} onClick={() => undo.mutate()}>
          Отменить скан
        </button>
        <button type="button" className="tsd-button tsd-button--primary" disabled={complete.isPending} onClick={() => complete.mutate()}>
          Завершить сборку
        </button>
      </footer>
    </main>
  )
}

export function TsdPage() {
  const [authorized, setAuthorized] = useState(() => Boolean(localStorage.getItem('tsd_access_token')))
  const [document, setDocument] = useState<TsdDocumentDetail | null>(null)
  if (!authorized) return <TsdLogin onReady={() => setAuthorized(true)} />
  if (document) return <TsdPicking initial={document} onBack={() => setDocument(null)} />
  return <TsdShipmentList onOpen={setDocument} />
}
