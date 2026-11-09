import { useDeferredValue, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { scansApi, type PackagingNode } from '../api/client'
import { Icon } from './Icon'

const labels = { BOX: 'Короб', GROUP: 'Блок', UNIT: 'Пачка', UNKNOWN: 'Упаковка' }

export function filterPackaging(nodes: PackagingNode[], query: string): PackagingNode[] {
  if (!query) return nodes
  return nodes.flatMap(node => {
    if (node.code.toLowerCase().includes(query)) return [node]
    const children = filterPackaging(node.children, query)
    return children.length ? [{ ...node, children, unit_count: node.unit_count ?? units(node) }] : []
  })
}

function units(node: PackagingNode): number {
  return node.kind === 'UNIT' ? 1 : node.children.reduce((sum, child) => sum + units(child), 0)
}

function PackagingBranch({ node, searching, root = false }: { node: PackagingNode; searching: boolean; root?: boolean }) {
  const [expanded, setExpanded] = useState(root)
  const [limit, setLimit] = useState(50)
  const hasChildren = node.children.length > 0
  const open = expanded || searching
  return <li className="packaging-tree__item">
    <div className="packaging-tree__row">
      {hasChildren ? <button type="button" className="packaging-tree__toggle"
        aria-expanded={open} aria-label={`${open ? 'Свернуть' : 'Раскрыть'} ${labels[node.kind].toLowerCase()} ${node.code}`}
        onClick={() => setExpanded(value => !value)} disabled={searching}>
        <Icon name="chevron" size={14} style={{ transform: open ? undefined : 'rotate(-90deg)' }} />
      </button> : <span className="packaging-tree__spacer" />}
      <div className="packaging-tree__identity">
        <span className="packaging-tree__kind">{labels[node.kind]}</span>
        <code className="packaging-tree__code">{node.code}</code>
      </div>
      {hasChildren && <span className="packaging-tree__quantity">Пачек: {node.unit_count ?? units(node)}</span>}
    </div>
    {node.fetched_at && <p className="hint packaging-tree__note">Данные ЧЗ от {new Date(node.fetched_at).toLocaleString('ru-RU')}</p>}
    {node.error && <p role="alert" className="alert alert--warn packaging-tree__note">{node.error}</p>}
    {node.kind === 'UNKNOWN' && <p className="hint packaging-tree__note">Тип упаковки не подтверждён. Связи показаны по ответу ЧЗ.</p>}
    {(node.incomplete || (!hasChildren && node.kind !== 'UNIT')) && <p className="hint packaging-tree__note">
      Вложенность ещё не получена.{node.expected_units ? ` Ранее определено пачек: ${node.expected_units}.` : ''} Нажмите «Обновить из ЧЗ».
    </p>}
    {hasChildren && open && <>
      <ul className="packaging-tree__children">{node.children.slice(0, limit).map(child =>
        <PackagingBranch key={child.code} node={child} searching={searching} />)}</ul>
      {node.children.length > limit && <button type="button" className="button button--sm packaging-tree__more"
        onClick={() => setLimit(value => value + 50)}>Показать ещё {Math.min(50, node.children.length - limit)}</button>}
    </>}
  </li>
}

export function PackagingComposition({ documentId, active }: { documentId: string | null; active: boolean }) {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [code, setCode] = useState('')
  const filter = useDeferredValue(search.trim().toLowerCase())
  const key = ['packaging-composition', documentId]
  const composition = useQuery({
    queryKey: key,
    queryFn: () => scansApi.composition(documentId!).then(response => response.data),
    enabled: active && !!documentId,
    refetchInterval: query => active && ['queued', 'running'].includes(query.state.data?.job.status ?? '') ? 1500 : false,
  })
  const refresh = useMutation({
    mutationFn: () => scansApi.refreshComposition(documentId!, code.trim() || undefined),
    onSuccess: () => { setCode(''); void qc.invalidateQueries({ queryKey: key }) },
  })
  const roots = composition.data?.roots ?? []
  const visible = useMemo(() => filterPackaging(roots, filter), [roots, filter])
  const busy = refresh.isPending || ['queued', 'running'].includes(composition.data?.job.status ?? '')
  const error = refresh.error || composition.error
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  if (!documentId) return <div className="flow-tabs__empty">Выберите документ, чтобы посмотреть состав упаковок.</div>
  return <section className="packaging-composition" aria-label="Состав блоков">
    <div className="packaging-composition__toolbar">
      <label className="field-label">Поиск по структуре
        <input type="search" className="ui-input" value={search} onChange={event => setSearch(event.target.value)}
          placeholder="Код короба, блока или пачки" />
      </label>
      <button type="button" className="button" disabled={busy || (!roots.length && !code.trim())}
        onClick={() => refresh.mutate()}><Icon name="refresh" size={15} /> {busy ? 'Получаем структуру…' : 'Обновить из ЧЗ'}</button>
    </div>
    <p className="hint">Раскройте короб или блок, чтобы увидеть вложенные упаковки и пачки. Поиск сохраняет путь к найденному коду.</p>
    <form className="packaging-composition__lookup" onSubmit={event => { event.preventDefault(); if (code.trim() && !busy) refresh.mutate() }}>
      <label className="field-label">Код упаковки для просмотра
        <input className="ui-input" value={code} maxLength={2048} disabled={busy}
          onChange={event => setCode(event.target.value)} placeholder="Вставьте код короба или блока" />
      </label>
      <button type="submit" className="button" disabled={!code.trim() || busy}>Получить состав</button>
    </form>
    <p className="hint">Этот запрос показывает структуру и не добавляет товар в сборку. Для получения данных нужен действующий вход в ЧЗ.</p>
    {busy && <p role="status">Запрашиваем вложенность упаковок в ЧЗ…</p>}
    {error && <p role="alert" className="alert alert--error">{detail || 'Не удалось загрузить структуру. Повторите запрос.'}</p>}
    {composition.data?.job.error && !busy && <p role="alert" className="alert alert--warn">{composition.data.job.error}</p>}
    {composition.isLoading && <p role="status">Загружаем сохранённую структуру…</p>}
    {!composition.isLoading && !roots.length && <div className="flow-tabs__empty">
      Структура упаковок ещё не сохранена. Укажите код короба или блока выше. Для старых загрузок промежуточные уровни могли не сохраниться.
    </div>}
    {roots.length > 0 && !visible.length && <p role="status">Код в сохранённой структуре не найден.</p>}
    <ul className="packaging-tree">{visible.map(node => <PackagingBranch key={node.code} node={node} searching={!!filter} root />)}</ul>
  </section>
}
