import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import api from '../api/client'
import { getOrganizationProfileId } from '../lib/organizationProfile'
import { getClientTimezone, timezoneLabel, timezoneOptions } from '../lib/clientTimezone'

type StateSettings = { state_id: string | null; order_state_id: string | null; shipment_timezone: string | null; shipment_date_on_collection_start: boolean; states: { id: string; name: string }[]; order_states: { id: string; name: string }[] }
type StateDraft = Pick<StateSettings, 'state_id' | 'order_state_id' | 'shipment_timezone' | 'shipment_date_on_collection_start'>
export function ShipmentStatusSettings() {
  const profileId = getOrganizationProfileId()
  const key = ['shipment-status-settings', profileId]
  const qc = useQueryClient()
  const [draft, setDraft] = useState<StateDraft | null>(null)
  const settings = useQuery({ queryKey: key, queryFn: () => api.get<StateSettings>('/organization-profiles/shipment-status-settings').then(r => r.data) })
  const save = useMutation({ mutationFn: () => api.put('/organization-profiles/shipment-status-settings', draft), onSuccess: () => { setDraft(null); void qc.invalidateQueries({ queryKey: key }) } })
  useEffect(() => { setDraft(null); save.reset() }, [profileId])
  const selected = draft ?? { state_id: settings.data?.state_id ?? null, order_state_id: settings.data?.order_state_id ?? null, shipment_timezone: settings.data?.shipment_timezone ?? null, shipment_date_on_collection_start: settings.data?.shipment_date_on_collection_start ?? false }
  const deviceTimezone = getClientTimezone()
  const fields = [{ key: 'state_id' as const, label: 'Статус отгрузки', options: settings.data?.states || [] },
                  { key: 'order_state_id' as const, label: 'Статус заказа покупателя', options: settings.data?.order_states || [] }]
  const missing = fields.some(field => selected[field.key] && !field.options.some(v => v.id === selected[field.key]))
  const error = settings.error || save.error
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return <section className="section">
    <h2>Дата и статусы при начале сборки</h2>
    <p className="hint">При открытии отгрузки для сборки можно изменить статус отгрузки и связанного заказа покупателя. Выбор отдельный для каждого документа и юрлица, применяется при открытии на ПК и ТСД. Повторное открытие и подключение второго ТСД не возвращают статус назад. Если у отгрузки нет связанного заказа, меняется только её статус.</p>
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
      <input type="checkbox" checked={selected.shipment_date_on_collection_start}
        disabled={!settings.data || settings.isFetching || save.isPending || !!settings.error}
        onChange={e => { setDraft({ ...selected, shipment_date_on_collection_start: e.target.checked }); save.reset() }} />
      <span>Менять дату отгрузки на текущую при начале сборки</span>
    </label>
    <p className="hint">По умолчанию выключено. Настройка действует для выбранного юрлица на ПК и ТСД. Дата счёта-фактуры автоматически не меняется; включайте только если это подходит вашему порядку формирования XML.</p>
    {selected.shipment_date_on_collection_start && <p className="hint">При первом нажатии «Начать сборку» дата и время отгрузки в МойСкладе заменяются текущими по выбранному часовому поясу, даже если статусы не меняются. Дата заказа покупателя остаётся прежней. Повторное открытие дату не меняет.</p>}
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
    })}
      {selected.shipment_date_on_collection_start && <label className="field-label">Часовой пояс юрлица
        <select className="ui-select" value={selected.shipment_timezone ?? ''} disabled={!settings.data || settings.isFetching || save.isPending || !!settings.error}
          onChange={e => { setDraft({ ...selected, shipment_timezone: e.target.value || null }); save.reset() }}>
          <option value="">Определять по устройству{deviceTimezone ? ` (${deviceTimezone})` : ''}</option>
          {timezoneOptions([selected.shipment_timezone, deviceTimezone]).map(zone => <option key={zone} value={zone}>{timezoneLabel(zone)}</option>)}
        </select>
      </label>}
    </div>
    {selected.shipment_date_on_collection_start && <p className="hint">Если сотрудник работает из другого города, задайте часовой пояс этого юрлица явно. Настройка отдельная для каждого юрлица и действует на ПК и ТСД.</p>}
    <button type="button" className="button button--primary mt-12" disabled={!draft || !settings.data || !!settings.error || save.isPending || missing} onClick={() => save.mutate()}>{save.isPending ? 'Сохраняем…' : 'Сохранить настройки'}</button>
    {settings.isLoading && <p role="status">Загружаем статусы…</p>}
    {save.isSuccess && <p role="status">Настройка сохранена.</p>}
  </section>
}
