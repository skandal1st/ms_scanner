import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { organizationProfilesApi } from '../api/client'
import { getOrganizationProfileId } from '../lib/organizationProfile'
import { preferredWorkplace } from '../lib/workplaceMode'

export function WorkplaceScannerSettings() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState('')
  const { data: profiles = [], isLoading, isError } = useQuery({queryKey:['organization-profiles'], queryFn:()=>organizationProfilesApi.list().then(r=>r.data)})
  const profile = profiles.find(p=>p.id === getOrganizationProfileId()) ?? profiles.find(p=>p.is_default) ?? profiles[0]
  const workplaces = profile?.workplaces.filter(w=>w.is_active) ?? []
  const selected = workplaces.find(w=>w.id === selectedId) ?? preferredWorkplace(workplaces)
  const save = useMutation({
    mutationFn: (mode: 'com' | 'tsd') => selected
      ? organizationProfilesApi.updateWorkplaceMode(selected.id, mode)
      : organizationProfilesApi.createWorkplace({organization_profile_id:profile!.id,name:'Основное рабочее место',is_default:true,scan_mode:mode}),
    onSuccess: async response => {
      setSelectedId(response.data.id)
      await qc.invalidateQueries({queryKey:['organization-profiles']})
    },
  })
  return <section className="section">
    <h2>Режим сборки отгрузок</h2>
    <p className="hint">COM-сканер работает в отдельном окне. В режиме ТСД сборка идёт на терминалах, а прогресс, проверка марок и отправка в МойСклад доступны в окне МойСклада. Изменение применяется при следующем открытии отгрузки.</p>
    {isLoading ? <p className="hint">Загружаем рабочие места…</p> : isError ? <p role="alert">Не удалось загрузить рабочие места.</p> : <>
      {workplaces.length > 0 && <label className="field mt-8" style={{maxWidth:420}}><span>Рабочее место</span>
        <select value={selected?.id ?? ''} disabled={save.isPending} onChange={e=>{setSelectedId(e.target.value);save.reset()}}>
          <option value="" disabled>Выберите рабочее место</option>
          {workplaces.map(w=><option key={w.id} value={w.id}>{w.name}</option>)}
        </select></label>}
      <label className="field mt-8" style={{maxWidth:420}}><span>Способ сканирования</span>
        <select value={selected?.scan_mode ?? 'com'} disabled={!profile || save.isPending || (workplaces.length > 0 && !selected)}
          onChange={e=>save.mutate(e.target.value as 'com' | 'tsd')}>
          <option value="com">COM-сканер — отдельное окно</option>
          <option value="tsd">ТСД — прогресс в окне МойСклада</option>
        </select></label>
      {!workplaces.length && <p className="hint">При выборе режима будет создано основное рабочее место.</p>}
      {save.isPending && <p role="status" className="hint">Сохраняем…</p>}
      {save.isSuccess && <p role="status" className="hint">Режим сохранён.</p>}
      {save.isError && <p role="alert" className="hint">Не удалось сохранить режим. Повторите выбор.</p>}
    </>}
  </section>
}
