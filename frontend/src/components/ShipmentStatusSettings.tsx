import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import api from '../api/client'
import { getOrganizationProfileId } from '../lib/organizationProfile'

export function ShipmentStatusSettings() {
  const profileId = getOrganizationProfileId()
  const key = ['shipment-status-settings', profileId]
  const qc = useQueryClient()
  const [draft, setDraft] = useState<string | null>(null)
  const settings = useQuery({ queryKey: key, queryFn: () => api.get<{ state_id: string | null; states: { id: string; name: string }[] }>('/organization-profiles/shipment-status-settings').then(r => r.data) })
  const save = useMutation({ mutationFn: () => api.put('/organization-profiles/shipment-status-settings', { state_id: draft || null }), onSuccess: () => { setDraft(null); void qc.invalidateQueries({ queryKey: key }) } })
  useEffect(() => { setDraft(null); save.reset() }, [profileId])
  const selected = draft ?? settings.data?.state_id ?? ''
  const missing = !!selected && !settings.data?.states.some(v => v.id === selected)
  const error = settings.error || save.error
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return <section className="section">
    <h2>Статус отгрузки после передачи в МойСклад</h2>
    <p className="hint">После успешной записи всех марок отгрузка получит выбранный статус. Настройка действует для этого юрлица при отправке с ПК и ТСД.</p>
    {error && <p role="alert">{typeof detail === 'string' ? detail : 'Не удалось загрузить или сохранить настройку.'} <button type="button" className="button button--sm" onClick={() => { save.reset(); void settings.refetch() }}>Повторить</button></p>}
    <label className="field-label">Статус в МойСкладе
      <select className="ui-select" value={selected} disabled={!settings.data || settings.isFetching || save.isPending || !!settings.error} onChange={e => { setDraft(e.target.value); save.reset() }}>
        <option value="">Не менять статус</option>
        {missing && <option value={selected} disabled>Сохранённый статус больше недоступен</option>}
        {settings.data?.states.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
      </select>
    </label>
    <button type="button" className="button button--primary mt-12" disabled={draft === null || !settings.data || !!settings.error || save.isPending || missing} onClick={() => save.mutate()}>{save.isPending ? 'Сохраняем…' : 'Сохранить статус'}</button>
    {settings.isLoading && <p role="status">Загружаем статусы отгрузок…</p>}
    {save.isSuccess && <p role="status">Настройка сохранена.</p>}
  </section>
}
