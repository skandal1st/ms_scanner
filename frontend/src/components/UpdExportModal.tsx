import { useEffect, useRef, useState } from 'react'
import { documentsApi } from '../api/client'
import type { UpdContext, UpdDraft, UpdExportOptions, UpdParty } from '../api/client'

async function errorMessage(error: unknown): Promise<string> {
  let data = (error as { response?: { data?: unknown } }).response?.data
  if (data instanceof Blob) {
    try { data = JSON.parse(await data.text()) } catch { /* network error */ }
  }
  const detail = (data as { detail?: unknown })?.detail
  return typeof detail === 'string' ? detail : 'Не удалось сформировать УПД. Проверьте реквизиты и повторите попытку.'
}

export function UpdExportModal({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [context, setContext] = useState<UpdContext | null>(null)
  const [options, setOptions] = useState<UpdExportOptions | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<UpdDraft | null>(null)
  const [draftLoaded, setDraftLoaded] = useState(false)
  const locked = !!draft && draft.status !== 'failed'
  useEffect(() => {
    dialog.current?.showModal()
    let live = true
    Promise.allSettled([documentsApi.updContext(documentId), documentsApi.updDraft(documentId)]).then(async ([source, saved]) => {
      if (!live) return
      setDraftLoaded(saved.status === 'fulfilled')
      if (saved.status === 'fulfilled' && saved.value.data) {
        setDraft(saved.value.data)
        setOptions(saved.value.data.status === 'failed' && source.status === 'fulfilled'
          ? { ...saved.value.data.options, number: source.value.data.number, document_date: source.value.data.document_date }
          : saved.value.data.options)
        setError(saved.value.data.error || '')
        if (source.status === 'fulfilled') setContext(source.value.data)
        return
      }
      if (saved.status === 'rejected') {
        setError(await errorMessage(saved.reason))
        return
      }
      if (source.status === 'rejected') { setError(await errorMessage(source.reason)); return }
      const data = source.value.data
      setContext(data)
      setOptions({ function: data.function, number: data.number, document_date: data.document_date,
        seller: data.seller, buyer: data.buyer, sender_id: '', receiver_id: '',
        signer_last_name: data.seller.last_name, signer_first_name: data.seller.first_name,
        signer_middle_name: data.seller.middle_name, signer_position: '',
        signer_is_principal: false, no_excise: false, cargo_parties_match: false })
    }).catch(async e => { const message = await errorMessage(e); if (live) setError(message) })
    return () => { live = false }
  }, [documentId])

  const field = (label: string, key: keyof UpdExportOptions, required = true, type = 'text') => options && (
    <label style={{ display: 'grid', gap: 4 }} key={key}>{label}
      <input className="ui-input" type={type} required={required} value={String(options[key])}
        disabled={busy || (context?.invoice_locked && (key === 'number' || key === 'document_date'))}
        onChange={e => setOptions({ ...options, [key]: e.target.value })} />
    </label>
  )
  const party = (label: string, key: 'seller' | 'buyer') => {
    if (!options) return null
    const fields: [string, keyof UpdParty, boolean][] = [['Наименование', 'name', true], ['ИНН', 'inn', true],
      ['КПП (для организации)', 'kpp', options[key].inn.length !== 12], ['Юридический адрес', 'address', true]]
    if (options[key].inn.length === 12) fields.push(['Фамилия ИП', 'last_name', true],
      ['Имя ИП', 'first_name', true], ['Отчество ИП', 'middle_name', false])
    return <fieldset disabled={busy} style={{ border: '1px solid var(--ms-border-light)', padding: 12, margin: '12px 0' }}>
      <legend>{label}</legend>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 10 }}>
        {fields.map(([title, name, required]) => <label style={{ display: 'grid', gap: 4 }} key={name}>{title}
          <input className="ui-input" value={options[key][name]} required={required}
            onChange={e => setOptions({ ...options, [key]: { ...options[key], [name]: e.target.value } })} />
        </label>)}
      </div>
    </fieldset>
  }
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!options || busy) return
    setBusy(true); setError('')
    try {
      const intent = ((event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null)?.value
      if (intent === 'draft') {
        const { data } = await documentsApi.createUpdDraft(documentId, options)
        setDraft(data); setOptions(data.options); setError(data.error || '')
        return
      }
      const response = draft ? await documentsApi.downloadUpdDraft(documentId) : await documentsApi.exportUpd(documentId, options)
      const disposition = String(response.headers['content-disposition'] || '')
      const filename = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
      const url = URL.createObjectURL(response.data)
      const a = document.createElement('a')
      a.href = url; a.download = filename ? decodeURIComponent(filename) : 'УПД.xml'
      a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      setError(await errorMessage(e))
      // A request timeout can occur after Saby accepted the XML. Restore the
      // persisted identity so the next action reconciles that exact upload.
      try { const { data } = await documentsApi.updDraft(documentId); if (data) { setDraft(data); setOptions(data.options) } } catch { /* keep original error */ }
    } finally { setBusy(false) }
  }
  return <dialog ref={dialog} className="popup__body" aria-labelledby="upd-title"
    style={{ width: 'min(760px, calc(100vw - 24px))', maxWidth: 760, maxHeight: '90vh', overflowY: 'auto' }}
    onCancel={e => { e.preventDefault(); if (!busy) onClose() }}>
    <button className="popup__close" type="button" aria-label="Закрыть" onClick={onClose} disabled={busy} />
    <h2 id="upd-title" className="popup__title">Сформировать XML УПД</h2>
    <form onSubmit={submit} className="popup__content">
      <p className="hint">УПД ФНС 5.03 из отгрузки МойСклад. Можно скачать XML или загрузить его в черновик СБИС. Подписание и отправка покупателю не выполняются.</p>
      {!options && !error && <p role="status">Загружаем реквизиты УПД…</p>}
      {draft?.status === 'created' && <p role="status" style={{ color: 'var(--st-ok-fg)' }}>Черновик СБИС создан и XML проверен. Без подписи и отправки.
        {draft.url && <> <a href={draft.url} target="_blank" rel="noopener noreferrer">Открыть в СБИС</a></>}</p>}
      {draft && draft.status !== 'created' && <p role="status">{draft.status === 'failed' ? 'СБИС отклонил загрузку. Проверьте реквизиты и повторите попытку.' : 'Идентификатор загрузки сохранён. Повторная попытка проверит этот же документ в СБИС.'}</p>}
      {options && <fieldset disabled={busy || locked} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        {context && <p>Отгрузка {context.shipment_name} · {context.positions} позиций · {(context.total / 100).toLocaleString('ru-RU', { style: 'currency', currency: 'RUB' })}</p>}
        {context?.warnings.map(w => <p key={w} role="note" style={{ color: 'var(--st-warn-fg)' }}>{w}</p>)}
        <label style={{ display: 'grid', gap: 4 }}>Вид УПД
          <select className="ui-input" disabled={busy} value={options.function}
            onChange={e => setOptions({ ...options, function: e.target.value as UpdExportOptions['function'],
              ...(context?.invoice_locked
                ? { number: context.number, document_date: context.document_date } : {}) })}>
            <option value="СЧФДОП">УПД со счётом-фактурой (СЧФДОП)</option>
            <option value="ДОП">Передаточный документ (ДОП)</option>
          </select>
        </label>
        <p className="hint">Номер и дата УПД совпадают с отгрузкой МойСклад и заполняются автоматически.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 10 }}>
          {field('Номер УПД', 'number')}{field('Дата УПД', 'document_date', true, 'date')}
          {field('Идентификатор ЭДО продавца', 'sender_id')}{field('Идентификатор ЭДО покупателя', 'receiver_id')}
        </div>
        {party('Продавец', 'seller')}{party('Покупатель', 'buyer')}
        <label style={{ display: 'block', margin: '12px 0' }}><input type="checkbox" required disabled={busy} checked={options.cargo_parties_match}
          onChange={e => setOptions({ ...options, cargo_parties_match: e.target.checked })} /> Грузоотправитель — продавец, грузополучатель — покупатель</label>
        <fieldset disabled={busy} style={{ border: '1px solid var(--ms-border-light)', padding: 12 }}>
          <legend>Подписант — руководитель или ИП</legend>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 10 }}>
            {field('Фамилия', 'signer_last_name')}{field('Имя', 'signer_first_name')}
            {field('Отчество', 'signer_middle_name', false)}{field('Должность', 'signer_position')}
          </div>
          <label style={{ display: 'block', marginTop: 12 }}><input type="checkbox" required checked={options.signer_is_principal}
            onChange={e => setOptions({ ...options, signer_is_principal: e.target.checked })} /> Подписант действует без доверенности</label>
        </fieldset>
        <label style={{ display: 'block', margin: '12px 0' }}><input type="checkbox" required disabled={busy} checked={options.no_excise}
          onChange={e => setOptions({ ...options, no_excise: e.target.checked })} /> Продавец не начисляет акциз по этой отгрузке</label>
      </fieldset>}
      {error && <p role="alert" style={{ color: 'var(--st-err-fg)' }}>{error}</p>}
      <div className="flex-row gap-8" style={{ justifyContent: 'flex-end', marginTop: 16, flexWrap: 'wrap' }}>
        <button type="button" className="button" disabled={busy} onClick={onClose}>Закрыть</button>
        <button type="submit" value="xml" className="button" disabled={!options || busy}>Скачать XML УПД</button>
        <button type="submit" value="draft" className="button button--primary" disabled={!options || !draftLoaded || busy || draft?.status === 'created'}>
          {busy ? 'Обрабатываем УПД…' : draft?.status === 'created' ? 'Черновик создан' : draft && ['uploading', 'uncertain', 'prepared'].includes(draft.status) ? 'Проверить загрузку в СБИС' : 'Создать черновик СБИС'}
        </button>
      </div>
    </form>
  </dialog>
}
