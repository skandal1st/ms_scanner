import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { organizationProfilesApi, type CustomerOrderFilter } from '../api/client'
import { getOrganizationProfileId } from '../lib/organizationProfile'

const blank = (): CustomerOrderFilter => ({ id: crypto.randomUUID(), name: '', project_id: null,
  project_name: null, sale_attribute_id: null, sale_dictionary_id: null, sale_value_id: null, sale_value_name: null })

function message(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : 'Не удалось сохранить или загрузить фильтры. Повторите попытку.'
}

export function CustomerOrderFiltersSettings() {
  const qc = useQueryClient()
  const profileId = getOrganizationProfileId()
  const [draft, setDraft] = useState<CustomerOrderFilter>(blank)
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(false)
  const filters = useQuery({ queryKey: ['customer-order-filters', profileId],
    queryFn: () => organizationProfilesApi.orderFilters().then(r => r.data) })
  const options = useQuery({ queryKey: ['customer-order-filter-options', profileId],
    queryFn: () => organizationProfilesApi.orderFilterOptions().then(r => r.data) })
  const save = useMutation({ mutationFn: (items: CustomerOrderFilter[]) => organizationProfilesApi.saveOrderFilters(items).then(r => r.data),
    onMutate: () => setSaved(false),
    onSuccess: (items) => {
      qc.setQueryData(['customer-order-filters', profileId], items)
      void qc.invalidateQueries({ queryKey: ['customer-orders'] })
      setDraft(blank()); setEditing(false); setSaved(true)
    },
  })
  const items = filters.data || []
  const duplicateName = items.some(item => item.id !== draft.id && item.name.toLocaleLowerCase() === draft.name.trim().toLocaleLowerCase())
  const busy = save.isPending || filters.isLoading || !!filters.error
  return <section className="section">
    <div className="section__head"><h2 style={{ margin: 0 }}>Фильтры заказов покупателей</h2></div>
    <p className="hint">Фильтры этого юрлица доступны на ПК и всех его ТСД. Если выбраны оба условия, заказ должен соответствовать и проекту, и полю «Где продажа».</p>
    {(filters.error || options.error || save.error) && <div className="alert alert--error" role="alert">{message(filters.error || options.error || save.error)}
      <button type="button" className="button button--sm" onClick={() => { save.reset(); void filters.refetch(); void options.refetch() }}>Повторить загрузку</button></div>}
    {options.data?.warnings.map(w => <p className="alert alert--warn" key={w}>{w}</p>)}
    {options.isLoading && <p role="status" className="hint">Загружаем проекты и значения «Где продажа»…</p>}
    {filters.isLoading && <p role="status" className="hint">Загружаем фильтры…</p>}
    {items.length > 0 && <ul className="order-filters-settings__list">{items.map(item => <li key={item.id}>
      <div><b>{item.name}</b><p className="hint">{[item.project_id ? `Проект: ${item.project_name || 'сохранённый проект'}` : '', item.sale_value_id ? `Где продажа: ${item.sale_value_name || 'сохранённое значение'}` : ''].filter(Boolean).join(' · ')}</p></div>
      <div className="field-row"><button type="button" className="button button--sm" disabled={busy} onClick={() => { setDraft(item); setEditing(true); setSaved(false); save.reset() }}>Изменить</button>
        <button type="button" className="button button--sm button--danger" disabled={busy} onClick={() => save.mutate(items.filter(v => v.id !== item.id))}>Удалить</button></div>
    </li>)}</ul>}
    {!filters.isLoading && !filters.error && items.length === 0 && <p className="hint">Фильтров пока нет. Добавьте первый ниже.</p>}
    <form className="order-filters-settings__form" onSubmit={e => { e.preventDefault(); if (!busy && !duplicateName && draft.name.trim() && (draft.project_id || draft.sale_value_id)) save.mutate([...items.filter(v => v.id !== draft.id), { ...draft, name: draft.name.trim() }]) }}>
      <label className="field-label">Название фильтра<input className="ui-input ui-input--block" value={draft.name} maxLength={100} required disabled={busy}
        placeholder="Например, Хорека · Питер" onChange={e => { setSaved(false); setDraft(v => ({ ...v, name: e.target.value })) }} /></label>
      <label className="field-label">Проект<select className="ui-input ui-input--block" value={draft.project_id || ''} disabled={busy || options.isLoading}
        onChange={e => { const item = options.data?.projects.find(p => p.id === e.target.value); setDraft(v => ({ ...v, project_id: item?.id || null, project_name: item?.name || null })); setSaved(false) }}>
        <option value="">Любой проект</option>
        {draft.project_id && !options.data?.projects.some(p => p.id === draft.project_id) && <option value={draft.project_id}>{draft.project_name || 'Сохранённый проект'}</option>}
        {options.data?.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select></label>
      <label className="field-label">Где продажа<select className="ui-input ui-input--block" value={draft.sale_value_id || ''} disabled={busy || options.isLoading}
        onChange={e => { const item = options.data?.sale_values.find(p => p.id === e.target.value); setDraft(v => ({ ...v, sale_value_id: item?.id || null, sale_value_name: item?.name || null,
          sale_attribute_id: item ? options.data!.sale_attribute_id : null, sale_dictionary_id: item ? options.data!.sale_dictionary_id : null })); setSaved(false) }}>
        <option value="">Любое значение</option>
        {draft.sale_value_id && !options.data?.sale_values.some(p => p.id === draft.sale_value_id) && <option value={draft.sale_value_id}>{draft.sale_value_name || 'Сохранённое значение'}</option>}
        {options.data?.sale_values.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select></label>
      {duplicateName && <p className="hint" role="status">Фильтр с таким названием уже существует.</p>}
      <div className="field-row"><button type="submit" className="button button--success" disabled={busy || duplicateName || !draft.name.trim() || !(draft.project_id || draft.sale_value_id) || (!editing && items.length >= 50)}>{save.isPending ? 'Сохраняем…' : editing ? 'Сохранить изменения' : 'Добавить фильтр'}</button>
        {editing && <button type="button" className="button" disabled={busy} onClick={() => { setDraft(blank()); setEditing(false); save.reset() }}>Отмена</button>}
        {saved && <span role="status">Фильтры сохранены</span>}</div>
    </form>
    <button type="button" className="button button--sm mt-12" disabled={options.isFetching} onClick={() => options.refetch()}>Обновить проекты и значения</button>
  </section>
}
