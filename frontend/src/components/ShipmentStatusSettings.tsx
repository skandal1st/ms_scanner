import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import api from '../api/client'
import { getOrganizationProfileId } from '../lib/organizationProfile'

type StateSettings = { state_id: string | null; order_state_id: string | null; states: { id: string; name: string }[]; order_states: { id: string; name: string }[] }
type StateDraft = Pick<StateSettings, 'state_id' | 'order_state_id'>
export function ShipmentStatusSettings() {
  const profileId = getOrganizationProfileId()
  const key = ['shipment-status-settings', profileId]
  const qc = useQueryClient()
  const [draft, setDraft] = useState<StateDraft | null>(null)
  const settings = useQuery({ queryKey: key, queryFn: () => api.get<StateSettings>('/organization-profiles/shipment-status-settings').then(r => r.data) })
  const save = useMutation({ mutationFn: () => api.put('/organization-profiles/shipment-status-settings', draft), onSuccess: () => { setDraft(null); void qc.invalidateQueries({ queryKey: key }) } })
  useEffect(() => { setDraft(null); save.reset() }, [profileId])
  const selected = draft ?? { state_id: settings.data?.state_id ?? null, order_state_id: settings.data?.order_state_id ?? null }
  const fields = [{ key: 'state_id' as const, label: 'Статус отгрузки', options: settings.data?.states || [] },
                  { key: 'order_state_id' as const, label: 'Статус заказа покупателя', options: settings.data?.order_states || [] }]
  const missing = fields.some(field => selected[field.key] && !field.options.some(v => v.id === selected[field.key]))
  const error = settings.error || save.error
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return <section className="section">
    <h2>Статусы после передачи в МойСклад</h2>
    <p className="hint">После записи всех марок можно изменить статус отгрузки и связанного заказа покупателя. Выбор отдельный для каждого документа и юрлица, применяется при отправке с ПК и ТСД. Если у отгрузки нет связанного заказа, меняется только её статус.</p>
    <p className="hint">Для смены статуса заказа покупателя решению нужно право обновления заказов. Обновите XML решения в кабинете МойСклада и переустановите решение.</p>
    {error && <p role="alert">{typeof detail === 'string' ? detail : 'Не удалось загрузить или сохранить настройку.'} <button type="button" className="button button--sm" onClick={() => { save.reset(); void settings.refetch() }}>Повторить</button></p>}
    <div className="order-filters-settings__form">{fields.map(field => {
      const value = selected[field.key] || ''
      const unavailable = !!value && !field.options.some(v => v.id === value)
      return <label key={field.key} className="field-label">{field.label}
        <select className="ui-select" value={value} disabled={!settings.data || settings.isFetching || save.isPending || !!settings.error} onChange={e => { setDraft({ ...selected, [field.key]: e.target.value || null }); save.reset() }}>
          <option value="">Не менять статус</option>
          {unavailable && <option value={value} disabled>Сохранённый статус больше недоступен</option>}
          {field.options.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      </label>
    })}</div>
    <button type="button" className="button button--primary mt-12" disabled={!draft || !settings.data || !!settings.error || save.isPending || missing} onClick={() => save.mutate()}>{save.isPending ? 'Сохраняем…' : 'Сохранить статусы'}</button>
    {settings.isLoading && <p role="status">Загружаем статусы…</p>}
    {save.isSuccess && <p role="status">Настройка сохранена.</p>}
  </section>
}