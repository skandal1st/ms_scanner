import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { shipmentCorrectionsApi, type ShipmentCorrectionDelta } from '../api/client'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { useTsdScannerFocus } from '../hooks/useTsdScannerFocus'

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
  const [adjust, setAdjust] = useState(false)
  const [preview, setPreview] = useState<ShipmentCorrectionDelta | null>(null)
  const [visibleScans, setVisibleScans] = useState(50)
  const inputRef = useRef<HTMLInputElement>(null)
  const requestPending = useRef(false)
  const sources = useQuery({ queryKey: ['shipment-correction-sources', terminal], queryFn: () => api.list().then(r => r.data) })
  const detail = useQuery({ queryKey: ['shipment-correction', terminal, id], enabled: Boolean(id), queryFn: () => api.get(id).then(r => r.data), refetchInterval: id ? 2000 : false })
  const doc = detail.data
  const blocked = busy || !doc || doc.locked || doc.status !== 'draft'
  useTsdScannerFocus(inputRef, blocked || !terminal, value => setCode(previous => previous + value))
  const forget = () => {
    localStorage.removeItem(storageKey)
    setId(''); setPosition(''); setPreview(null); setError(''); setNotice(''); setCode('')
    void sources.refetch()
  }
  const refresh = () => qc.invalidateQueries({ queryKey: ['shipment-correction', terminal, id] })
  const errorText = (e: unknown) => (e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Операция не выполнена. Проверьте сеть и повторите.'
  const run = async (action: () => Promise<unknown>) => {
    if (requestPending.current) return
    requestPending.current = true
    setBusy(true); setError(''); setNotice('')
    try { await action(); await refresh() }
    catch (e) { setError(errorText(e)) }
    finally { requestPending.current = false; setBusy(false) }
  }
  const add = () => {
    const raw = normalizeScannerInput(code).trim()
    if (blocked || !raw) return
    if (!position) { setError('Выберите позицию для новой марки'); return }
    void run(async () => {
      const { data } = await api.scan(id, raw, position)
      setCode(''); setPreview(null)
      setNotice(data.duplicate ? 'Эта марка уже есть в исправлении' : 'Марка добавлена. Перед сохранением проверьте новые марки.')
    })
  }
  const acceptRef = useRef(add)
  acceptRef.current = add
  useEffect(() => {
    if (!code.trim() || blocked || !position) return
    const timer = window.setTimeout(() => acceptRef.current(), 500)
    return () => window.clearTimeout(timer)
  }, [code, blocked, position])
  useEffect(() => { if (!blocked && position) inputRef.current?.focus({ preventScroll: true }) }, [blocked, position])
  const submit = (event: FormEvent) => { event.preventDefault(); add() }
  const newUnchecked = doc?.scans.some(s => !s.existing && ['scanned', 'pending'].includes(s.status))
  return <main className={`shipment-corrections${terminal ? ' tsd-shell' : ''}`}>
    <header className={terminal ? 'tsd-header' : 'flex-row gap-8'}>
      <button type="button" className="tsd-button" onClick={onClose}>К отгрузкам</button>
      <h1>Исправить отгрузку</h1>
    </header>
    <p className="hint">Замените ошибочные марки или добавьте недостающие. В МойСклад уйдут только изменения. Отправленный УПД через ЭДО этим действием не исправляется.</p>
    {(error || doc?.error_message) && <p role="alert" className="tsd-alert tsd-alert--error">{error || doc?.error_message}</p>}
    {notice && <p role="status" className="tsd-alert">{notice}</p>}
    {!id ? <>
      <label>Найти отгрузку<input className="input" value={search} onChange={e => setSearch(e.target.value)} placeholder="Номер или контрагент" /></label>
      {sources.isLoading && <p>Загружаем отправленные отгрузки…</p>}
      {sources.error && <p role="alert">Не удалось загрузить отгрузки. <button className="tsd-button" onClick={() => sources.refetch()}>Повторить</button></p>}
      <div className="tsd-position-list">{sources.data?.filter(d => d.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(source => <button type="button" className="tsd-position" key={source.id} disabled={busy} onClick={() => void run(async () => {
        const { data } = await api.open(source.id); setId(data.id); localStorage.setItem(storageKey, data.id)
      })}><strong>{source.name}</strong><span>{source.resume ? 'Продолжить исправление' : 'Открыть исправление'}</span></button>)}</div>
      {sources.data?.length === 0 && <p>Отправленных отгрузок пока нет.</p>}
    </> : !doc ? <>
      <p>{detail.error ? 'Не удалось открыть исправление.' : 'Загружаем состав отгрузки…'}</p>
      <button className="tsd-button" onClick={() => detail.refetch()}>Повторить</button>
      <button className="tsd-button" onClick={forget}>К списку исправлений</button>
    </> : <>
      <h2>Отгрузка {doc.name}</h2>
      {doc.status === 'accepted' ? <>
        <p role="status" className="tsd-alert">Исправления сохранены в МойСклад.</p>
        <button type="button" className="tsd-button tsd-button--primary" onClick={forget}>Следующее исправление</button>
      </> : <>
        {doc.status === 'processing' && <p role="status">Сохраняем изменения… {doc.processing_progress ? `${doc.processing_progress.sent}/${doc.processing_progress.total}` : ''}</p>}
        {doc.locked && <p className="hint">Версия зафиксирована для отправки. При сбое повторите сохранение: уже выполненные изменения не отправятся повторно.</p>}
        {doc.locked && doc.status === 'draft' && <button type="button" className="tsd-button" disabled={busy} onClick={() => void run(async () => {
          const { data } = await api.rebase(id)
          setId(data.id); localStorage.setItem(storageKey, data.id); setPreview(null); setPosition(''); setCode('')
          setNotice('Состав обновлён из МС. Оставшиеся правки перенесены; просмотрите изменения перед сохранением заново.')
        })}>Обновить из МС и продолжить исправление</button>}
        <label>Позиция для новых марок<select className="input" value={position} disabled={blocked} onChange={e => { setPosition(e.target.value); setPreview(null) }}>
          <option value="">Выберите товар</option>{doc.positions.map((p, i) => <option key={p.id} value={p.id}>{p.name} · {p.quantity} шт. · строка {i + 1}</option>)}
        </select></label>
        <form className="correction-scan-form" onSubmit={submit}>
          <input className="input" ref={inputRef} aria-label="Новая марка" value={code} readOnly={blocked} onChange={e => setCode(e.target.value)} placeholder="Сканируйте отдельную марку" autoComplete="off" autoCapitalize="off" spellCheck={false} inputMode="text" />
          <button type="submit" className="tsd-button" disabled={blocked || !position || !code.trim()}>Добавить</button>
        </form>
        <p className="hint">Состав упаковок не меняется. Удаление упаковки удалит её вместе с вложенными марками.</p>
        <button type="button" className="tsd-button" disabled={blocked || !newUnchecked} onClick={() => void run(async () => { await api.verify(id); setPreview(null); setNotice('Проверяем новые марки…') })}>Проверить новые марки</button>
        <label style={{ display: 'block', margin: '12px 0' }}><input type="checkbox" checked={adjust} disabled={blocked} onChange={e => { setAdjust(e.target.checked); setPreview(null) }} /> Изменить количество товаров по добавленным и удалённым маркам</label>
        <div className="tsd-input-actions">
          <button type="button" className="tsd-button tsd-button--primary" disabled={busy || doc.status !== 'draft'} onClick={() => void run(async () => { const { data } = await api.preview(id, adjust); setPreview(data) })}>Просмотреть изменения</button>
          <button type="button" className="tsd-button" disabled={busy || doc.status !== 'draft'} onClick={() => void run(async () => { await api.cancel(id); forget() })}>Отменить исправление</button>
        </div>
        {preview && <section className="tsd-alert" aria-label="Изменения перед отправкой">
          <h3>Добавить {preview.added.length}, удалить {preview.removed.length}</h3>
          {(['removed', 'added'] as const).map(action => preview[action].map((change, index) => <p key={`${action}:${index}`}><strong>{action === 'removed' ? 'Удалить' : 'Добавить'}: {change.product_name}</strong><br /><code style={{ overflowWrap: 'anywhere' }}>{change.code}</code>{change.package && <span> · Упаковка целиком</span>}</p>))}
          {preview.quantities.map((q, i) => <p key={i}>Количество {q.product_name}: {q.before} → {q.after}</p>)}
          {!preview.quantities.length && <p>Количества товаров сохраняются.</p>}
          <button type="button" className="tsd-button tsd-button--primary" disabled={busy || doc.status !== 'draft' || !preview.added.length && !preview.removed.length && !preview.quantities.length} onClick={() => void run(async () => { await api.save(id, adjust, preview.preview_hash); setPreview(null) })}>{doc.locked ? 'Продолжить сохранение' : 'Сохранить исправления'}</button>
        </section>}
        <h3>Марки отгрузки ({doc.scans.length})</h3>
        <div className="tsd-mark-list">{doc.scans.slice(0, visibleScans).map(scan => <article className="tsd-mark" key={scan.id}>
          <strong>{scan.product_name || doc.positions.find(p => p.id === scan.position_id)?.name}</strong>
          <code>{scan.code}</code><span>{scan.existing ? 'Из МойСклада' : scan.error_message || (['valid', 'overflow'].includes(scan.status) ? 'Новая · проверена' : 'Новая · требуется проверка')}</span>
          <button type="button" className="tsd-button tsd-button--danger" disabled={blocked} onClick={() => void run(async () => { await api.remove(id, scan.id); setPreview(null) })}>{scan.package ? 'Удалить упаковку с вложенными марками' : 'Удалить марку'}</button>
        </article>)}</div>
        {doc.scans.length > visibleScans && <button type="button" className="tsd-button" onClick={() => setVisibleScans(count => count + 50)}>Показать ещё 50 марок</button>}
      </>}
    </>}
  </main>
}
