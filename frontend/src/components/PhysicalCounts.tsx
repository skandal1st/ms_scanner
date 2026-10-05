import { useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { countApi, type CountMode, type CountCreate, type CountProgress } from '../api/physicalCounts'
import { tsdModeLabels } from '../api/client'
import { useTsdScannerFocus } from '../hooks/useTsdScannerFocus'
import { useTsdSound } from '../hooks/useTsdSound'
import { normalizeScannerInput } from '../lib/scannerLayout'

function errorText(error: unknown) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : null
  return typeof detail === 'string' ? detail : 'Не удалось выполнить действие. Проверьте связь и повторите.'
}

export function PhysicalCounts({ mode, terminal = false, documentId, deviceId, onBack }: { mode: CountMode; terminal?: boolean; documentId?: string; deviceId?: string; onBack?: () => void }) {
  const api = useMemo(() => countApi(terminal), [terminal])
  const qc = useQueryClient()
  const scope = terminal ? localStorage.getItem('tsd_access_token')?.slice(-16) : localStorage.getItem('organization_profile_id')
  const key = ['physical-counts', terminal, scope, mode, documentId]
  const [activeId, setActiveId] = useState<string | null>(null)
  const [store, setStore] = useState('')
  const [states, setStates] = useState<string[]>([])
  const [brand, setBrand] = useState('')
  const [selected, setSelected] = useState<string | undefined>()
  const [code, setCode] = useState('')
  const [quantity, setQuantity] = useState('1')
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [confirmation, setConfirmation] = useState<'brand' | 'session' | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const retryIntent = useRef<{ key: string; id: string } | null>(null)
  const sound = useTsdSound()
  const sessions = useQuery({ queryKey: key, queryFn: () => api.list(mode, documentId).then(r => r.data), refetchInterval: 5000 })
  const options = useQuery({ queryKey: [...key, 'options'], queryFn: () => api.options().then(r => r.data), enabled: !activeId && (mode === 'inventory' || (!terminal && Boolean(documentId))) })
  const docs = useQuery({ queryKey: [...key, 'acceptances'], queryFn: () => api.acceptances().then(r => r.data), enabled: terminal && mode === 'acceptance' && !activeId })
  const detail = useQuery({ queryKey: [...key, activeId, 'detail'], queryFn: () => api.detail(activeId!).then(r => r.data), enabled: Boolean(activeId),
    refetchInterval: q => q.state.data?.status === 'preparing' ? 2000 : false })
  const progressKey = [...key, activeId, 'progress']
  const progress = useQuery({ queryKey: progressKey, queryFn: () => api.progress(activeId!).then(r => r.data), enabled: Boolean(activeId), refetchInterval: 2000 })
  const data = progress.data || detail.data
  const rows = detail.data?.plan || []
  const brands = useMemo(() => [...new Set(rows.map(v => v.folder_name))], [rows])
  useEffect(() => { if (mode === 'acceptance') setBrand('Приёмка') }, [mode])
  useEffect(() => { if (!store && options.data?.stores.length === 1) setStore(options.data.stores[0].id) }, [options.data, store])
  const open = (id: string) => { setActiveId(id); setBrand(mode === 'acceptance' ? 'Приёмка' : ''); setSelected(undefined); setMessage(null) }
  const fail = (error: unknown) => { setMessage({ text: errorText(error), error: true }); sound.play('error') }
  const create = useMutation({ mutationFn: (body: CountCreate) => api.create(body).then(r => r.data), onSuccess: v => { open(v.id); void qc.invalidateQueries({ queryKey: key }) }, onError: fail })
  const updated = (v: CountProgress) => { qc.setQueryData(progressKey, v); void qc.invalidateQueries({ queryKey: key, exact: true }) }
  const scan = useMutation({ networkMode: 'always', mutationFn: (args: { code: string; qty?: number }) => {
      const intent = JSON.stringify([activeId, args.code, selected, args.qty || 1, brand])
      if (retryIntent.current?.key !== intent) retryIntent.current = { key: intent, id: crypto.randomUUID() }
      return api.scan(activeId!, args.code, selected, args.qty || 1, mode === 'inventory' ? brand : undefined, retryIntent.current!.id).then(r => r.data)
    },
    onSuccess: v => { retryIntent.current = null; updated(v); setCode(''); const row = rows.find(r => r.key === v.product_key); const over = row && (v.counts[row.key] || 0) > row.expected_qty
      setMessage({ text: v.duplicate ? 'Этот скан уже посчитан' : `${row?.product_name || 'Добавлено'}${over ? ' · сверх ожидаемого' : ''}`, error: Boolean(v.duplicate || over) }); sound.play(v.duplicate || over ? 'error' : 'ok') }, onError: error => { if (axios.isAxiosError(error) && error.response) retryIntent.current = null; fail(error) } })
  const remove = useMutation({ mutationFn: (id: string) => api.remove(activeId!, id).then(r => r.data), onSuccess: updated, onError: fail })
  const finishBrand = useMutation({ mutationFn: () => api.brand(activeId!, brand).then(r => r.data), onSuccess: v => { updated(v); setSelected(undefined); setMessage({ text: 'Сверка бренда завершена. Выберите следующий бренд.', error: false }) }, onError: fail })
  const finish = useMutation({ mutationFn: () => api.complete(activeId!).then(r => r.data), onSuccess: () => { void qc.invalidateQueries({ queryKey: key }); setMessage({ text: 'Сверка сохранена. Результат доступен на ПК.', error: false }) }, onError: fail })
  const download = useMutation({ mutationFn: () => api.export(activeId!), onError: fail })
  const reviewed = data?.settings.reviewed_brands || []
  const blocked = !terminal || !activeId || data?.status !== 'active' || !brand || reviewed.includes(brand) || Boolean(confirmation) || scan.isPending || remove.isPending || finish.isPending || finishBrand.isPending || !navigator.onLine
  useTsdScannerFocus(input, blocked, value => setCode(v => v + value))
  const shown = mode === 'inventory' ? rows.filter(v => v.folder_name === brand) : rows
  return <section className={terminal ? 'tsd-shell physical-count' : 'section physical-count'}>
    {terminal && onBack && <button className="tsd-button" onClick={onBack}>‹ К выбору режима</button>}
    <h2>{terminal ? tsdModeLabels[mode] : mode === 'acceptance' ? 'Сверка приёмки на ТСД' : 'Физическая инвентаризация на ТСД'}</h2>
    {!activeId ? <>
      <p className="hint">{mode === 'acceptance' ? 'Загрузите XML на ПК, затем откройте приёмку на ТСД. Здесь сохраняется фактическая сверка позиций.' : 'Выгрузите остатки склада, добавьте проведённые отгрузки выбранных статусов и сверяйте товары по брендам.'}</p>
      {mode === 'inventory' && <div className="physical-count-setup">
        <label className="field">Склад<select value={store} onChange={e => setStore(e.target.value)}><option value="">Выберите склад</option>{options.data?.stores.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
        <fieldset><legend>Товары в этих отгрузках ещё на складе</legend><p className="hint">Добавляем только проведённые отгрузки. Непроведённые уже входят в остаток.</p>
          {options.data?.states.map(v => <label className="physical-count-check" key={v.id}><input type="checkbox" checked={states.includes(v.id)} onChange={e => setStates(old => e.target.checked ? [...old, v.id] : old.filter(id => id !== v.id))} />{v.name}</label>)}
        </fieldset>
        <p className="hint">Остатки и отгрузки всего выбранного склада, по всем юрлицам аккаунта МойСклад.</p>
        <button className="button button--primary" disabled={!store || create.isPending} onClick={() => create.mutate({ mode, store_id: store, include_state_ids: states })}>Выгрузить остатки и начать сверку</button>
        {options.error && <p role="alert">{errorText(options.error)}</p>}
      </div>}
      {terminal && mode === 'acceptance' && <><h3>Загруженные приёмки</h3>{docs.data?.map(v => <button className="tsd-order" key={v.id} disabled={create.isPending} onClick={() => create.mutate({ mode, document_id: v.id })}><strong>{v.name}</strong><span>{new Date(v.created_at).toLocaleDateString('ru-RU')} · {v.positions} позиций</span></button>)}{docs.data?.length === 0 && <p className="hint">Загруженных приёмок нет. Проверьте юрлицо и склад рабочего места ТСД.</p>}{docs.error && <p role="alert">{errorText(docs.error)}</p>}</>}
      {documentId && !terminal && <><label className="field">Склад сверки (для XML без поступления МС)<select value={store} onChange={e => setStore(e.target.value)}><option value="">Склад из поступления / без ограничения</option>{options.data?.stores.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label><button className="button" disabled={create.isPending} onClick={() => create.mutate({ mode, document_id: documentId, store_id: store || undefined })}>Подготовить сверку для ТСД</button></>}
      <h3>Сессии сверки</h3>
      {sessions.data?.map(v => <button className="tsd-order" key={v.id} onClick={() => open(v.id)}><strong>{v.name}</strong><span>{new Date(v.created_at).toLocaleString('ru-RU')} · {v.status === 'active' ? 'В работе' : v.status === 'completed' ? 'Завершена' : v.status === 'preparing' ? 'Выгрузка…' : v.status === 'superseded' ? 'XML обновлён' : 'Ошибка выгрузки'}</span></button>)}
      {sessions.error && <p role="alert">{errorText(sessions.error)}</p>}
    </> : <>
      <button className="tsd-button" onClick={() => { setActiveId(null); setCode(''); setMessage(null) }}>‹ К списку сверок</button>
      <h3>{data?.name || 'Загрузка…'}</h3>
      {data?.status === 'preparing' && <p role="status">Выгружаем остатки и отгрузки из МойСклада…</p>}
      {data?.error_message && <p role="alert">{data.error_message}</p>}
      {detail.error && <p role="alert">{errorText(detail.error)}</p>}
      {progress.error && <p role="alert">{errorText(progress.error)}</p>}
      {data?.settings.snapshot_at && <p className="hint">Снимок: {new Date(data.settings.snapshot_at).toLocaleString('ru-RU')} · {data.settings.store_name}{data.settings.included_states?.length ? ` · Отгрузки: ${data.settings.included_states.map(v => v.name).join(', ')}` : ''}</p>}
      {mode === 'inventory' && rows.length > 0 && <label className="field">Бренд / группа товаров<select value={brand} disabled={Boolean(confirmation) || scan.isPending || finishBrand.isPending} onChange={e => { setBrand(e.target.value); setSelected(undefined); setCode('') }}><option value="">Выберите бренд</option>{brands.map(v => <option key={v} value={v}>{v}{reviewed.includes(v) ? ' · проверен' : ''}</option>)}</select></label>}
      {terminal && data?.status === 'active' && <section className="tsd-scan-dock">
        <form onSubmit={e => { e.preventDefault(); if (!blocked && code.trim()) scan.mutate({ code: normalizeScannerInput(code) }) }}><input ref={input} aria-label="Код для сверки" value={code} onChange={e => setCode(e.target.value)} inputMode="none" autoComplete="off" disabled={blocked} placeholder={brand ? 'Сканируйте код' : 'Сначала выберите бренд'} /><button disabled={blocked || !code.trim()} className="tsd-button" type="submit">Добавить</button></form>
        <div className="tsd-dock-meta"><span>{selected ? shown.find(v => v.key === selected)?.product_name : 'Авто по GTIN'}</span>{selected && <button className="tsd-button" onClick={() => setSelected(undefined)}>Авто</button>}<button data-tsd-sound className="tsd-button" onClick={() => void sound.toggle()}>{sound.active ? 'Звук вкл.' : 'Звук выкл.'}</button></div>
      </section>}
      {message && <p role="status" className={`tsd-alert ${message.error ? 'tsd-alert--warn' : ''}`}>{message.text}</p>}
      {selected && terminal && data?.status === 'active' && <div className="physical-count-manual"><label className="field">Количество вручную<input inputMode="decimal" type="number" min="0.001" step="0.001" value={quantity} onChange={e => setQuantity(e.target.value)} /></label><button className="tsd-button" disabled={blocked || !Number.isFinite(Number(quantity)) || Number(quantity) <= 0} onClick={() => scan.mutate({ code: `MANUAL:${crypto.randomUUID()}`, qty: Number(quantity) })}>Добавить количество</button></div>}
      <div className="tsd-position-list">{shown.map(v => { const actual = data?.counts[v.key] || 0; return <button key={v.key} disabled={!terminal || data?.status !== 'active' || reviewed.includes(v.folder_name) || scan.isPending} className={`tsd-position tsd-progress--${actual > v.expected_qty ? 'overflow' : actual === v.expected_qty ? 'done' : actual > 0 ? 'partial' : 'empty'}`} aria-pressed={selected === v.key} onClick={() => setSelected(old => old === v.key ? undefined : v.key)}><strong>{v.product_name}</strong><span>{(v.gtins || [v.gtin]).filter(Boolean).join(', ')}</span><b>{actual} / {v.expected_qty} шт.{selected === v.key ? ' · Выбрана для сканирования' : ''}</b>{mode === 'inventory' && <small>МС: {v.base_qty} · в отгрузках: {v.shipment_qty}</small>}</button> })}</div>
      {terminal && <><details><summary>Последние сканы ({data?.scans.length || 0})</summary>{data?.scans.filter(v => !selected || v.product_key === selected).map(v => <div className="physical-count-observation" key={v.id}><strong>{rows.find(r => r.key === v.product_key)?.product_name} · {v.quantity} шт.</strong><code>{v.code}</code>{v.device_id === deviceId && <button disabled={remove.isPending || data.status !== 'active' || reviewed.includes(rows.find(r => r.key === v.product_key)?.folder_name || '')} className="tsd-button tsd-button--danger" onClick={() => remove.mutate(v.id)}>Удалить свой скан</button>}</div>)}</details>
        {data?.status === 'active' && <div className="tsd-input-actions">{mode === 'inventory' && <button className="tsd-button" disabled={blocked} onClick={() => setConfirmation('brand')}>Бренд проверен</button>}<button className="tsd-button tsd-button--primary" disabled={Boolean(confirmation) || scan.isPending || remove.isPending || finish.isPending || finishBrand.isPending || (mode === 'inventory' && !reviewed.length)} onClick={() => setConfirmation('session')}>Завершить сверку</button></div>}</>}
      {confirmation && <div role="dialog" aria-label="Завершение сверки" className="tsd-alert">
        <p>{confirmation === 'brand' ? `Завершить сверку «${brand}»? Непросканированные позиции будут отмечены как отсутствующие.` : 'Сохранить и завершить сверку? Остатки МойСклада автоматически не изменяются.'}</p>
        <div className="tsd-input-actions"><button className="tsd-button" onClick={() => setConfirmation(null)}>Продолжить сканирование</button><button className="tsd-button tsd-button--primary" onClick={() => { const action = confirmation; setConfirmation(null); if (action === 'brand') finishBrand.mutate(); else finish.mutate() }}>Подтвердить завершение</button></div>
      </div>}
      {rows.length > 0 && !terminal && <button className="button" disabled={download.isPending} onClick={() => download.mutate()}>Скачать отчёт CSV</button>}
      {terminal && rows.length > 0 && <p className="hint">Отчёт доступен на ПК: {mode === 'inventory' ? 'Инвентаризация' : 'Приёмка → Сверка приёмки на ТСД'}.</p>}
      {data?.status === 'completed' && <p className="hint">Сверка завершена. Отчёт сохранён.</p>}
    </>}
    {!activeId && message && <p role="alert">{message.text}</p>}
  </section>
}
