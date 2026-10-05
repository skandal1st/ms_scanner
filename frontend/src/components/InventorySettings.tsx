import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { countApi } from '../api/physicalCounts'
import { getOrganizationProfileId } from '../lib/organizationProfile'

export function InventorySettings() {
  const api = countApi(false)
  const qc = useQueryClient()
  const profileId = getOrganizationProfileId()
  const key = ['inventory-settings', profileId]
  const options = useQuery({ queryKey: key, queryFn: () => api.options().then(r => r.data) })
  const [draft, setDraft] = useState<string[] | null>(null)
  useEffect(() => { setDraft(null) }, [profileId])
  const selected = draft || options.data?.default_include_state_ids || []
  const save = useMutation({ mutationFn: () => api.saveSettings(selected), onSuccess: () => {
    setDraft(null); void qc.invalidateQueries({ queryKey: key }); void qc.invalidateQueries({ queryKey: ['physical-counts'] })
  } })
  return <section className="section">
    <h2>Инвентаризация: товары в отгрузках</h2>
    <p className="hint">Выберите статусы отгрузок, товары которых ещё физически на складе. К остатку прибавятся только проведённые отгрузки выбранного склада. Непроведённые уже входят в остаток МойСклада. Выбор сохраняется для этого юрлица и применяется к новым инвентаризациям на ПК и ТСД.</p>
    {options.isLoading && <p>Загружаем статусы отгрузок…</p>}
    {(options.error || save.error) && <p role="alert">Не удалось загрузить или сохранить настройки. <button className="button button--sm" onClick={() => { save.reset(); void options.refetch() }}>Повторить</button></p>}
    <fieldset disabled={options.isLoading || !!options.error || save.isPending}><legend>Добавлять к остатку</legend>
      {options.data?.states.map(v => <label className="physical-count-check" key={v.id}><input type="checkbox" checked={selected.includes(v.id)} onChange={e => { save.reset(); setDraft(e.target.checked ? [...selected, v.id] : selected.filter(id => id !== v.id)) }} />{v.name}</label>)}
      <button className="button button--primary" disabled={draft === null} onClick={() => save.mutate()}>Сохранить статусы</button>
    </fieldset>
    {save.isSuccess && <p role="status">Настройка сохранена.</p>}
  </section>
}
