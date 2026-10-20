import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { shipmentCorrectionsApi, type ShipmentCorrectionDelta, type ShipmentCorrectionDetail } from '../api/client'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { verificationLabel } from '../lib/scanVerification'
import { useTsdScannerFocus } from '../hooks/useTsdScannerFocus'
import { useSerialScanner } from '../hooks/useSerialScanner'
import { useResizableWidth } from '../hooks/useResizableWidth'
import { FlowWorkspaceTabs, type FlowWorkspaceTab } from './FlowWorkspaceTabs'
import { Icon } from './Icon'
import { CodeSearchModal } from './CodeSearchModal'

type CorrectionScan = ShipmentCorrectionDetail['scans'][number]

export function ShipmentCorrections({ terminal = false, onClose }: { terminal?: boolean; onClose: () => void }) {
  const api = useMemo(() => shipmentCorrectionsApi(terminal), [terminal])
  const qc = useQueryClient()
  const storageKey = terminal ? 'tsd_correction_document_id' : 'shipment_correction_document_id'
  const [id, setId] = useState(() => localStorage.getItem(storageKey) || '')
  const [search, setSearch] = useState('')
  const [position, setPosition] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<ShipmentCorrectionDelta | null>(null)
  const [visibleScans, setVisibleScans] = useState(50)
  const [tab, setTab] = useState<FlowWorkspaceTab>('summary')
  const [markSearch, setMarkSearch] = useState('')
  const [replacement, setReplacement] = useState<CorrectionScan | null>(null)
  const [manualOpen, setManualOpen] = useState(terminal)
  const [searchOpen, setSearchOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const requestPending = useRef(0)
  const scanQueue = useRef(Promise.resolve())
  const { width, startResize } = useResizableWidth('shipment_correction_left_width', 420, { min: 300, max: 700, reserveRight: 400 })
  const sources = useQuery({ queryKey: ['shipment-correction-sources', terminal], queryFn: () => api.list().then(r => r.data) })
  const detail = useQuery({ queryKey: ['shipment-correction', terminal, id], enabled: Boolean(id), queryFn: () => api.get(id).then(r => r.data), refetchInterval: id ? 2000 : false })
  const doc = detail.data
  const locked = !doc || doc.locked || doc.status !== 'draft'
  const blocked = busy || locked
  useTsdScannerFocus(inputRef, blocked || !terminal || Boolean(preview), value => setCode(previous => previous + value))
  const forget = () => {
    localStorage.removeItem(storageKey)
    setId(''); setPosition(''); setPreview(null); setError(''); setNotice(''); setCode(''); setReplacement(null)
    void sources.refetch()
  }
  const refresh = () => qc.invalidateQueries({ queryKey: ['shipment-correction', terminal, id] })
  const errorText = (e: unknown) => (e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Операция не выполнена. Проверьте сеть и повторите.'
  const run = (action: () => Promise<unknown>) => {
    requestPending.current += 1
    setBusy(true)
    const task = scanQueue.current.then(async () => {
      setError(''); setNotice('')
      try { await action(); await refresh() }
      catch (e) { setError(errorText(e)) }
      finally { requestPending.current -= 1; setBusy(requestPending.current > 0) }
    })
    scanQueue.current = task
    return task
  }
  // Capture the document and position at scan time; serialize COM requests.
  const enqueue = (value: string) => {
    const raw = normalizeScannerInput(value).trim()
    if (!raw) return
    if (locked || preview) { setError('Сканирование недоступно. Вернитесь к редактированию марок.'); return }
    if (replacement && requestPending.current) { setError('Дождитесь завершения замены марки.'); return }
    const documentId = id, positionId = position || undefined, replaceId = replacement?.id
    setCode('')
    void run(async () => {
        const { data } = await api.scan(documentId, raw, positionId, replaceId)
        setPreview(null)
        if (data.duplicate) setError('Эта марка уже есть в исправлении. Исходная марка сохранена.')
        else if (replaceId && !data.replaced) setError('Новая марка не прошла проверку формата или уже используется. Исходная марка сохранена; удалите ошибочный скан.')
        else {
          if (replaceId) setReplacement(null)
          setNotice(replaceId ? 'Марка заменена. Проверьте новую марку перед сохранением.' : 'Марка добавлена. Проверьте новые марки перед сохранением.')
        }
    })
  }
  const serial = useSerialScanner({ enabled: !terminal && Boolean(id), onCode: enqueue })
  const acceptRef = useRef(enqueue)
  acceptRef.current = enqueue
  useEffect(() => {
    if (!code.trim() || blocked || preview) return
    const timer = window.setTimeout(() => acceptRef.current(code), 500)
    return () => window.clearTimeout(timer)
  }, [code, blocked, position, preview])
  useEffect(() => { if (!blocked && manualOpen && !preview) inputRef.current?.focus({ preventScroll: true }) }, [blocked, position, manualOpen, preview])
  useEffect(() => { setVisibleScans(50) }, [position, markSearch, id])
  const submit = (event: FormEvent) => { event.preventDefault(); enqueue(code) }
  const unchecked = doc?.scans.filter(s => !s.existing && ['scanned', 'pending'].includes(s.status)).length ?? 0
  const invalid = doc?.scans.filter(s => !s.existing && !['valid', 'overflow', 'scanned', 'pending'].includes(s.status)).length ?? 0
  const added = doc?.scans.filter(s => !s.existing).length ?? 0
  const removed = doc ? Math.max(0, doc.baseline_mark_count - doc.scans.filter(s => s.existing).length) : 0
  const rows = useMemo(() => doc?.positions.map(p => {
    const scans = doc.scans.filter(s => s.position_id === p.id)
    return { ...p, scans, count: scans.reduce((n, s) => n + (s.package ? s.box_quantity ?? 0 : 1), 0), unknown: scans.some(s => s.package && s.box_quantity == null) }
  }) ?? [], [doc])
  const total = rows.reduce((n, p) => n + p.quantity, 0)
  const count = rows.reduce((n, p) => n + p.count, 0)
  const filtered = doc?.scans.filter(s => (!position || s.position_id === position) && `${s.code} ${s.product_name || ''}`.toLocaleLowerCase().includes(markSearch.toLocaleLowerCase())) ?? []
  const choosePosition = (value: string) => { setPosition(value); setReplacement(null); setCode('') }
  const remove = (scan: CorrectionScan) => void run(async () => {
    await api.remove(id, scan.id); setPreview(null)
    if (replacement?.id === scan.id) setReplacement(null)
    setNotice('Марка удалена из исправления. Изменения отправятся в МойСклад после сохранения.')
  })
  const review = () => void run(async () => { const { data } = await api.preview(id); setPreview(data) })
  return <main className={`acc-page correction-workspace${terminal ? ' correction-workspace--terminal' : ''}`}>
    <header className="acc-header">
      <div className="flex-row gap-8">
        <button type="button" className="button button--sm" disabled={busy} onClick={onClose}>К отгрузкам</button>
        <h1 className="acc-header__title">Исправить отгрузку</h1>
        {doc && <span className={`badge ${doc.status === 'accepted' ? 'badge--ok' : 'badge--warn'}`}>{doc.status === 'accepted' ? 'Сохранено' : doc.status === 'processing' ? 'Сохраняется' : 'Исправление'}</span>}
      </div>
      <span className="acc-header__doc">{doc ? `Отгрузка ${doc.name}` : 'Выберите отправленную отгрузку'}</span>
    </header>
    <div className="correction-workspace__info hint">Исправление меняет только марки. Товары, количества и цены сохраняются. Отправленный УПД через ЭДО этим действием не исправляется.</div>
    {(error || doc?.error_message) && <div role="alert" className="alert alert--error correction-workspace__message">{error || doc?.error_message}</div>}
    {notice && <div role="status" className="alert alert--info correction-workspace__message">{notice}</div>}
    {!id ? <section className="correction-picker">
      <label className="field-label" htmlFor="correction-search">Найти отгрузку</label>
      <input id="correction-search" className="ui-input ui-input--block" value={search} onChange={e => setSearch(e.target.value)} placeholder="Номер или контрагент" />
      {sources.isLoading && <p role="status">Загружаем отправленные отгрузки…</p>}
      {sources.error && <p role="alert">Не удалось загрузить отгрузки. <button className="button" onClick={() => void sources.refetch()}>Повторить</button></p>}
      <div className="correction-picker__list">{sources.data?.filter(d => d.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(source => <button type="button" className="button correction-picker__item" key={source.id} disabled={busy} onClick={() => void run(async () => {
        const { data } = await api.open(source.id); setId(data.id); localStorage.setItem(storageKey, data.id)
      })}><strong>{source.name}</strong><span className="hint">{source.resume ? 'Продолжить исправление' : 'Открыть исправление'}</span></button>)}</div>
      {sources.data?.length === 0 && <p>Отправленных отгрузок пока нет.</p>}
    </section> : !doc ? <section className="correction-picker">
      <p>{detail.error ? 'Не удалось открыть исправление.' : 'Загружаем состав отгрузки…'}</p>
      <button className="button" onClick={() => void detail.refetch()}>Повторить</button>{' '}
      <button className="button" onClick={forget}>К списку исправлений</button>
    </section> : doc.status === 'accepted' ? <section className="correction-picker">
      <p role="status" className="alert alert--ok">Исправления сохранены в МойСклад.</p>
      <button type="button" className="button button--primary" onClick={forget}>Следующее исправление</button>
    </section> : <>
      <div className="acc-body">
        <aside className="acc-left" style={{ width }}>
          <div><div className="field-label">Документ</div><strong>Отгрузка {doc.name}</strong><p className="hint">Сканируйте недостающие марки. Для замены выберите исходную марку во вкладке «Марки».</p></div>
          <label className="correction-target"><span className="field-label">Сканировать в товар</span>
            <select className="ui-input ui-input--block" value={position} disabled={blocked || Boolean(replacement)} onChange={e => choosePosition(e.target.value)}>
              <option value="">Авто (по GTIN / плану)</option>{doc.positions.map((p, i) => <option key={p.id} value={p.id}>{p.name} · {p.quantity} шт. · строка {i + 1}</option>)}
            </select>
          </label>
          {replacement && <div className="alert alert--info correction-replacement"><strong>Замена марки</strong><code>{replacement.code}</code><span>Сканируйте новую марку для этого товара.</span><button type="button" className="button button--sm" disabled={busy} onClick={() => { setReplacement(null); setCode('') }}>Отменить замену</button></div>}
          <div className="scan-input">
            <div className="field-label">Сканирование</div>
            {!terminal && <div className="scan-input__field scan-input__com-status correction-com">
              <span className={`badge ${serial.connected ? 'badge--ok' : 'badge--warn'}`}><span className="badge__dot" />COM-порт {serial.connected ? 'подключён' : 'не подключён'}</span>
              <button type="button" className="button button--sm button--primary" disabled={!serial.supported} onClick={() => void (serial.connected ? serial.disconnect() : serial.requestConnect())}>{serial.connected ? 'Отключить' : 'Подключить COM-порт'}</button>
              <button type="button" className="button button--sm" disabled={blocked} onClick={() => setManualOpen(v => !v)}>{manualOpen ? 'Скрыть ручной ввод' : 'Ввести вручную'}</button>
              {serial.error && <p className="hint">{serial.error}</p>}
              {!serial.supported && <p className="hint">COM-сканирование доступно в Chrome и Edge по HTTPS.</p>}
            </div>}
            {manualOpen && <form className="correction-scan-form" onSubmit={submit}>
              <input className="scan-input__field" ref={inputRef} aria-label="Новая марка" value={code} readOnly={blocked} onChange={e => setCode(e.target.value)} placeholder={replacement ? 'Сканируйте заменяющую марку' : 'Сканируйте отдельную марку'} autoComplete="off" autoCapitalize="off" spellCheck={false} inputMode="text" />
              <button type="submit" className="button button--primary" disabled={blocked || !code.trim()}>{replacement ? 'Заменить' : 'Добавить'}</button>
            </form>}
            {!position && <p className="hint">Товар определяется по GTIN. Для одинаковых строк выберите позицию вручную.</p>}
          </div>
          {!terminal && <button type="button" className="button" onClick={() => setSearchOpen(true)}><Icon name="filter" size={15} /> Поиск марки</button>}
          <div className="stats">{[['Всего марок', doc.scans.length], ['Добавлено', added], ['Удалено', removed], ['Ожидают проверки', unchecked], ['Ошибок', invalid]].map(([label, value]) => <div className="stats__row" key={label}><span className="stats__label">{label}</span><strong className="stats__value">{value}</strong></div>)}</div>
          <p className="hint">Упаковки удаляются целиком вместе с вложенными марками. Состав упаковок исправляется в МойСкладе.</p>
          {doc.locked && <div className="alert alert--warn">Версия зафиксирована для отправки. При сбое повторите сохранение: выполненные изменения повторно не отправятся.</div>}
          {doc.locked && doc.status === 'draft' && <button type="button" className="button" disabled={busy} onClick={() => void run(async () => {
            const { data } = await api.rebase(id)
            setId(data.id); localStorage.setItem(storageKey, data.id); setPreview(null); setPosition(''); setCode(''); setReplacement(null)
            setNotice('Состав обновлён из МС. Просмотрите оставшиеся изменения перед сохранением.')
          })}>Обновить из МС и продолжить исправление</button>}
        </aside>
        {!terminal && <div className="acc-split" onMouseDown={startResize} role="separator" aria-orientation="vertical" title="Потяните, чтобы изменить ширину панелей" />}
        <div className="acc-right">
          <FlowWorkspaceTabs summaryLabel="Сборка" summaryCount={`${count}/${total}`} marksCount={doc.scans.length} issueCount={invalid} pendingCount={unchecked} active={tab} onChange={setTab}
            summary={<div className="correction-summary">
              <div className="correction-summary__head"><strong>Состав отгрузки</strong><span className="hint">Марок: {count} / {total}</span></div>
              <p className="hint">Количество товара зафиксировано. Нажмите на товар, чтобы выбрать его для сканирования.</p>
              {rows.map((row, i) => <div className={`correction-product${position === row.id ? ' correction-product--selected' : ''}`} key={row.id}>
                <button type="button" className="correction-product__select" disabled={blocked || Boolean(replacement)} onClick={() => choosePosition(row.id)} aria-pressed={position === row.id}>
                  <span>{i + 1}. {row.name}</span><span className="correction-product__count">{row.unknown ? 'Неизвестно' : row.count} из {row.quantity}</span>
                  <span className="correction-product__track"><span style={{ width: `${row.quantity ? Math.min(100, row.count / row.quantity * 100) : 0}%` }} /></span>
                </button>
                <button type="button" className="button button--sm" onClick={() => { if (!replacement) setPosition(row.id); setTab('marks') }}>Марки ({row.scans.length})</button>
              </div>)}
            </div>}
            marks={<div className="correction-marks">
              <div className="correction-marks__filters"><input className="ui-input" aria-label="Найти марку в отгрузке" placeholder="Код или товар" value={markSearch} onChange={e => setMarkSearch(e.target.value)} />{position && <button type="button" className="button button--sm" disabled={Boolean(replacement)} onClick={() => setPosition('')}>Все товары</button>}</div>
              {position && <p className="hint">{doc.positions.find(p => p.id === position)?.name}</p>}
              <div className="correction-marks__list">{filtered.slice(0, visibleScans).map(scan => <article className="correction-mark" key={scan.id}>
                <div className="correction-mark__content"><strong>{scan.product_name || doc.positions.find(p => p.id === scan.position_id)?.name}</strong><code>{scan.code}</code><span className={scan.existing ? 'hint' : `badge ${['valid', 'overflow'].includes(scan.status) ? 'badge--ok' : ['scanned', 'pending'].includes(scan.status) ? 'badge--warn' : 'badge--err'}`}>{scan.existing ? 'Из МойСклада' : scan.error_message || verificationLabel(scan)}</span>{!scan.existing && scan.verification?.owner_result !== 'match' && <small className="hint">{scan.verification?.owner_reason}</small>}</div>
                <div className="correction-mark__actions"><button type="button" className="button button--sm" disabled={blocked || scan.package} onClick={() => { setReplacement(scan); setPosition(scan.position_id); setManualOpen(true); setCode(''); setNotice(''); setError('') }}>Заменить</button><button type="button" className="button button--sm" disabled={blocked} onClick={() => remove(scan)}><Icon name="trash" size={14} />{scan.package ? 'Удалить упаковку' : 'Удалить'}</button></div>
              </article>)}</div>
              {!filtered.length && <p className="flow-tabs__empty">Марок по выбранному фильтру нет.</p>}
              {filtered.length > visibleScans && <button type="button" className="button" onClick={() => setVisibleScans(n => n + 50)}>Показать ещё 50 марок</button>}
            </div>} />
        </div>
      </div>
      <footer className="acc-footer">
        <button type="button" className="button" disabled={busy || doc.status !== 'draft'} onClick={() => void run(async () => { await api.cancel(id); forget() })}>Отменить исправление</button>
        <div className="acc-footer__spacer" />
        {unchecked > 0 && <button type="button" className="button" disabled={blocked} onClick={() => void run(async () => { await api.verify(id); setPreview(null); setNotice('Проверяем новые марки…') })}>Проверить новые марки ({unchecked})</button>}
        <button type="button" className="button button--primary" disabled={busy || doc.status !== 'draft' || Boolean(replacement)} onClick={review}>{doc.status === 'processing' ? `Сохраняем… ${doc.processing_progress?.sent ?? 0}/${doc.processing_progress?.total ?? 0}` : 'Просмотреть изменения'}</button>
      </footer>
      {preview && <div className="popup"><div className="popup__overlay" onClick={busy ? undefined : () => setPreview(null)} /><dialog open className="popup__body correction-preview" aria-label="Изменения перед отправкой" onCancel={e => { e.preventDefault(); if (!busy) setPreview(null) }}>
        <button type="button" className="popup__close" aria-label="Закрыть изменения" disabled={busy} onClick={() => setPreview(null)} /><h2 className="popup__title">Добавить {preview.added.length}, удалить {preview.removed.length}</h2>
        <div className="popup__content">{(['removed', 'added'] as const).map(action => preview[action].map((change, index) => <p key={`${action}:${index}`}><strong>{action === 'removed' ? 'Удалить' : 'Добавить'}: {change.product_name}</strong><br /><code>{change.code}</code>{change.package && <span> · Упаковка целиком</span>}</p>))}<p className="hint">Товары, количества и цены сохраняются. Отправленный УПД через ЭДО не изменится.</p>
          {preview.quantities.length > 0 && <p role="alert" className="alert alert--error">Эта версия исправления меняет количество товаров. Обновите состав из МС перед сохранением.</p>}
          <div className="flex-row gap-8"><button type="button" className="button" disabled={busy} onClick={() => setPreview(null)}>Вернуться к маркам</button><button type="button" className="button button--primary" disabled={busy || doc.status !== 'draft' || Boolean(preview.quantities.length) || !preview.added.length && !preview.removed.length} onClick={() => void run(async () => { await api.save(id, preview.preview_hash); setPreview(null) })}>{doc.locked ? 'Продолжить сохранение' : 'Сохранить исправления'}</button></div>
        </div>
      </dialog></div>}
    </>}
    {!terminal && <CodeSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />}
  </main>
}
