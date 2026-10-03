import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { organizationProfilesApi, type CustomerOrderFilter } from '../api/client'
import { getOrganizationProfileId } from '../lib/organizationProfile'

const blank = (): CustomerOrderFilter => ({ id: crypto.randomUUID(), name: '', projects: [], sale_values: [], project_id: null,
  project_name: null, sale_attribute_id: null, sale_dictionary_id: null, sale_value_id: null, sale_value_name: null })

function message(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : 'Не удалось сохранить или загрузить фильтры. Повторите попытку.'
}

type Choice = { id: string; name: string }

function MultiChoice({ title, options, selected, disabled, onChange }: {
  title: string; options: Choice[]; selected: Choice[]; disabled: boolean; onChange: (values: Choice[]) => void
}) {
  const [search, setSearch] = useState('')
  const choices = [...selected.filter(value => !options.some(option => option.id === value.id)), ...options]
  const visible = choices.filter(value => value.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  return <fieldset className="order-filter-choices" disabled={disabled}>
    <legend>{title}</legend>
    <div className="order-filter-choices__head"><span className="hint">{selected.length ? `Выбрано: ${selected.length}` : 'Любое значение'}</span>
      {selected.length > 0 && <button type="button" className="button button--sm" onClick={() => onChange([])}>Снять выбор</button>}</div>
    <input className="ui-input ui-input--block" aria-label={`Поиск: ${title}`} placeholder="Поиск по названию" value={search} onChange={e => setSearch(e.target.value)} />
    <div className="order-filter-choices__values">{visible.map(value => <label key={value.id}>
      <input type="checkbox" checked={selected.some(item => item.id === value.id)} onChange={e => onChange(e.target.checked ? [...selected, value] : selected.filter(item => item.id !== value.id))} />
      <span>{value.name || 'Сохранённое значение'}</span></label>)}
      {!visible.length && <p className="hint">Ничего не найдено</p>}</div>
  </fieldset>
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
    <p className="hint">Фильтры этого юрлица доступны на ПК и всех его ТСД. Можно выбрать несколько проектов и мест продаж. В каждом поле подходит любое выбранное значение; между полями действует условие «и». Без выбора поле не ограничивает список заказов.</p>
    {(filters.error || options.error || save.error) && <div className="alert alert--error" role="alert">{message(filters.error || options.error || save.error)}
      <button type="button" className="button button--sm" onClick={() => { save.reset(); void filters.refetch(); void options.refetch() }}>Повторить загрузку</button></div>}
    {options.data?.warnings.map(w => <p className="alert alert--warn" key={w}>{w}</p>)}
    {options.isLoading && <p role="status" className="hint">Загружаем проекты и значения «Где продажа»…</p>}
    {filters.isLoading && <p role="status" className="hint">Загружаем фильтры…</p>}
    {items.length > 0 && <ul className="order-filters-settings__list">{items.map(item => <li key={item.id}>
      <div><b>{item.name}</b><p className="hint">{[item.projects.length ? `Проекты: ${item.projects.map(v => v.name || 'сохранённый проект').join(', ')}` : '', item.sale_values.length ? `Где продажа: ${item.sale_values.map(v => v.name || 'сохранённое значение').join(', ')}` : ''].filter(Boolean).join(' · ')}</p></div>
      <div className="field-row"><button type="button" className="button button--sm" disabled={busy} onClick={() => { setDraft(item); setEditing(true); setSaved(false); save.reset() }}>Изменить</button>
        <button type="button" className="button button--sm button--danger" disabled={busy} onClick={() => save.mutate(items.filter(v => v.id !== item.id))}>Удалить</button></div>
    </li>)}</ul>}
    {!filters.isLoading && !filters.error && items.length === 0 && <p className="hint">Фильтров пока нет. Добавьте первый ниже.</p>}
    <form className="order-filters-settings__form" onSubmit={e => { e.preventDefault(); if (!busy && !duplicateName && draft.name.trim() && (draft.projects.length || draft.sale_values.length)) save.mutate([...items.filter(v => v.id !== draft.id), { ...draft, name: draft.name.trim() }]) }}>
      <label className="field-label">Название фильтра<input className="ui-input ui-input--block" value={draft.name} maxLength={100} required disabled={busy}
        placeholder="Например, Хорека · Питер" onChange={e => { setSaved(false); setDraft(v => ({ ...v, name: e.target.value })) }} /></label>
      <MultiChoice title="Проекты" options={options.data?.projects || []} selected={draft.projects} disabled={busy || options.isLoading}
        onChange={projects => { setDraft(v => ({ ...v, projects })); setSaved(false) }} />
      <MultiChoice title="Где продажа" options={options.data?.sale_values || []} selected={draft.sale_values} disabled={busy || options.isLoading}
        onChange={sale_values => { setDraft(v => ({ ...v, sale_values,
          sale_attribute_id: sale_values.length ? options.data?.sale_attribute_id || v.sale_attribute_id : null,
          sale_dictionary_id: sale_values.length ? options.data?.sale_dictionary_id || v.sale_dictionary_id : null })); setSaved(false) }} />
      {duplicateName && <p className="hint" role="status">Фильтр с таким названием уже существует.</p>}
      <div className="field-row"><button type="submit" className="button button--success" disabled={busy || duplicateName || !draft.name.trim() || !(draft.projects.length || draft.sale_values.length) || (!editing && items.length >= 50)}>{save.isPending ? 'Сохраняем…' : editing ? 'Сохранить изменения' : 'Добавить фильтр'}</button>
        {editing && <button type="button" className="button" disabled={busy} onClick={() => { setDraft(blank()); setEditing(false); save.reset() }}>Отмена</button>}
        {saved && <span role="status">Фильтры сохранены</span>}</div>
    </form>
    <button type="button" className="button button--sm mt-12" disabled={options.isFetching} onClick={() => options.refetch()}>Обновить проекты и значения</button>
  </section>
}
