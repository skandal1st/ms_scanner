import { Fragment, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { gtinControlApi, type GtinAuditCard, type GtinAuditFinding, type GtinAuditStatus } from '../api/client'
import { Icon } from '../components/Icon'
import './GtinControl.css'

const queryKey = ['gtin-control']
const number = (value = 0) => value.toLocaleString('ru-RU')
const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString('ru-RU') : '—'
const categories: Record<string, { label: string; description: string; severity: string }> = {
  other_product: { label: 'GTIN у другого товара', description: 'В сохранённой карточке этого GTIN нет. В действующем каталоге найдена другая карточка.', severity: 'error' },
  invalid_product_id: { label: 'Некорректный ID карточки', description: 'Сохранённый идентификатор не является UUID карточки товара МС.', severity: 'error' },
  missing_product: { label: 'Карточка не найдена', description: 'Сохранённая карточка отсутствует в полном каталоге МС, включая архив.', severity: 'error' },
  ambiguous_mapping: { label: 'Неоднозначный GTIN', description: 'Код указан у нескольких действующих товаров. Автоматически выбрать правильный нельзя.', severity: 'warn' },
  archived_product: { label: 'Карточка в архиве', description: 'GTIN есть в сохранённой карточке, но она архивная. Историческая привязка может быть верной.', severity: 'muted' },
  unconfirmed_gtin: { label: 'GTIN не подтверждён', description: 'В сохранённой карточке кода нет, а однозначная действующая замена не найдена. Это может быть ручная привязка.', severity: 'warn' },
  renamed_product: { label: 'Изменилось название', description: 'GTIN и карточка совпадают. Отличается только сохранённое название товара.', severity: 'info' },
  duplicate_active: { label: 'Несколько действующих карточек', description: 'Один GTIN указан у двух или более действующих товаров МС.', severity: 'warn' },
  duplicate_with_archive: { label: 'Совпадение с архивом', description: 'GTIN есть в действующей карточке и в архивных товарах.', severity: 'muted' },
  duplicate_archived: { label: 'Дубли в архиве', description: 'Этот GTIN указан у нескольких архивных товаров.', severity: 'muted' },
}
const phases: Record<string, string> = {
  queued: 'Проверка ожидает запуска', active_catalog: 'Читаем действующие карточки МС',
  archived_catalog: 'Читаем архивные карточки МС', analyzing: 'Сравниваем GTIN и сохранённые привязки',
}
function errorText(error: unknown, fallback = 'Не удалось загрузить контроль GTIN. Проверьте связь и повторите попытку.') {
  return (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    || fallback
}

function Product({ card, gtin }: { card: GtinAuditCard; gtin: string }) {
  const link = card.url && /^https:\/\/online\.moysklad\.ru\//.test(card.url) ? card.url : null
  return <div className="gtin-control__product">
    <div>{link ? <a href={link} target="_blank" rel="noopener noreferrer">{card.name}<Icon name="link" size={12} /></a> : card.name}</div>
    <div className="gtin-control__product-meta">
      {card.archived && <span className="badge badge--muted">Архив</span>}
      {card.pack_gtins.includes(gtin) && <span>GTIN упаковки</span>}
      <span title={card.id}>ID: {card.id}</span>
    </div>
  </div>
}

function NationalCatalogCheck({ runId, gtin, onClose }: { runId: string; gtin: string; onClose: () => void }) {
  const cache = useQueryClient()
  const key = ['gtin-national-catalog', runId, gtin]
  const result = useQuery({ queryKey: key, queryFn: () => gtinControlApi.nationalCatalog(runId, gtin).then(r => r.data),
    staleTime: 10 * 60 * 1000, retry: false })
  const refresh = useMutation({ mutationFn: () => gtinControlApi.nationalCatalog(runId, gtin, true).then(r => r.data),
    onSuccess: data => cache.setQueryData(key, data) })
  const data = result.data
  return <section className="gtin-control__nk" aria-label={`Национальный каталог: ${gtin}`}>
    <div className="gtin-control__nk-heading"><h2>Национальный каталог · <code>{gtin}</code></h2>
      <button type="button" className="button button--sm" onClick={onClose}>Скрыть</button></div>
    {(result.isLoading || refresh.isPending) && <p role="status">Получаем сведения о товаре из Национального каталога…</p>}
    {(result.error || refresh.error) && <div className="alert alert--error" role="alert">
      {errorText(refresh.error || result.error, 'Не удалось получить сведения Национального каталога. Повторите проверку позже.')}{data && ' Ниже показан предыдущий полученный ответ.'}
      <button type="button" className="button button--sm" disabled={refresh.isPending} onClick={() => refresh.mutate()}>Повторить проверку</button>
    </div>}
    {data && <>
      {data.status === 'found' ? <dl className="gtin-control__nk-data">
        <div><dt>Наименование товара</dt><dd>{data.good_name || 'Наименование не указано'}</dd></div>
        <div><dt>Бренд</dt><dd>{data.brand_name || 'Не указан'}</dd></div>
        <div><dt>Категория</dt><dd>{data.category || 'Не указана'}</dd></div>
      </dl> : <p>Карточка с этим GTIN в Национальном каталоге не найдена. Это само по себе не подтверждает ошибку марки.</p>}
      <div className="gtin-control__nk-footer"><span>Данные НК от {date(data.checked_at)}{data.source === 'cache' ? ' · сохранённый ответ' : ''}</span>
        <button type="button" className="button button--sm" disabled={result.isFetching || refresh.isPending} onClick={() => refresh.mutate()}>
          <Icon name="refresh" size={13} />Обновить из НК
        </button></div>
      <p className="gtin-control__muted">Сравните сведения НК с карточками МС. Сохранённые привязки и карточки товаров не изменяются.</p>
    </>}
  </section>
}

function Finding({ item, mapping, runId }: { item: GtinAuditFinding; mapping: boolean; runId: string }) {
  const category = categories[item.category]
  const [checking, setChecking] = useState(false)
  return <Fragment><tr>
    <td className="gtin-control__code"><code>{item.gtin}</code>
      <button type="button" className="button button--sm gtin-control__nk-button" onClick={() => setChecking(value => !value)}
        aria-expanded={checking} aria-label={`Проверить GTIN ${item.gtin} в Национальном каталоге`}>
        <Icon name="check" size={13} />Проверить в НК
      </button>
    </td>
    {mapping && <td>
      <div>{item.saved_product_name || 'Без сохранённого названия'}</div>
      <div className="gtin-control__product-meta">ID: {item.saved_product_id || '—'}</div>
      {item.saved_card && item.saved_card.name !== item.saved_product_name &&
        <div className="gtin-control__current-name">Сейчас в МС: {item.saved_card.name}</div>}
      {item.saved_card?.archived && <span className="badge badge--muted">Архив</span>}
      {item.saved_card && <details className="gtin-control__details">
        <summary>GTIN сохранённой карточки</summary>
        <p>{item.saved_card.gtins.join(', ') || 'GTIN отсутствуют'}</p>
      </details>}
    </td>}
    <td>
      {item.candidates.slice(0, 2).map(card => <Product key={card.id} card={card} gtin={item.gtin} />)}
      {item.candidates.length > 2 && <details className="gtin-control__details">
        <summary>Ещё карточек: {item.candidates.length - 2}</summary>
        {item.candidates.slice(2).map(card => <Product key={card.id} card={card} gtin={item.gtin} />)}
      </details>}
      {!item.candidates.length && <span className="gtin-control__muted">Карточки с этим GTIN не найдены</span>}
    </td>
    <td className="gtin-control__reason">
      <span className={`badge badge--${category?.severity || 'warn'}`}>{category?.label || item.category}</span>
      <p>{category?.description}</p>
    </td>
  </tr>{checking && <tr><td colSpan={mapping ? 4 : 3} className="gtin-control__nk-cell">
    <NationalCatalogCheck runId={runId} gtin={item.gtin} onClose={() => setChecking(false)} />
  </td></tr>}</Fragment>
}

export function GtinControlPage() {
  const cache = useQueryClient()
  const [tab, setTab] = useState<'mapping' | 'duplicate'>('mapping')
  const [category, setCategory] = useState('')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [archive, setArchive] = useState(false)
  const [page, setPage] = useState(1)
  useEffect(() => {
    const timeout = window.setTimeout(() => { setQuery(search.trim()); setPage(1) }, 300)
    return () => clearTimeout(timeout)
  }, [search])
  const state = useQuery({ queryKey, queryFn: () => gtinControlApi.status().then(r => r.data),
    refetchInterval: query => ['queued', 'running'].includes(query.state.data?.job?.status || '') ? 2000 : false,
  })
  const job = state.data?.job
  const report = state.data?.report
  const running = Boolean(job && ['queued', 'running'].includes(job.status))
  const staleJob = Boolean(running && job && Date.now() - Date.parse(job.updated_at) > 30 * 60 * 1000)
  const start = useMutation({ mutationFn: () => gtinControlApi.refresh().then(r => r.data),
    onMutate: () => cache.cancelQueries({ queryKey }),
    onSuccess: job => cache.setQueryData<GtinAuditStatus>(queryKey, old => ({ report: old?.report || null, job })),
    onSettled: () => { void cache.invalidateQueries({ queryKey }) },
  })
  const findings = useQuery({
    queryKey: ['gtin-control-findings', report?.id, tab, category, query, archive, page],
    enabled: Boolean(report),
    queryFn: () => gtinControlApi.findings({ run_id: report!.id, kind: tab, category,
      q: query, include_archived: archive, page }).then(r => r.data),
  })
  const summary = report?.summary || {}
  const items = findings.data?.items || []
  const total = findings.data?.total || 0
  const pages = Math.max(1, Math.ceil(total / 50))
  const selectTab = (next: 'mapping' | 'duplicate') => { setTab(next); setCategory(''); setPage(1) }
  const visibleError = start.error || state.error
  return <main className="gtin-control">
    <header className="gtin-control__header">
      <div><h1>Контроль GTIN</h1><p>Сверка сохранённых привязок и штрихкодов карточек МойСклад.</p></div>
      <button type="button" className="button button--primary" onClick={() => start.mutate()}
        disabled={start.isPending || (running && !staleJob) || state.isLoading}>
        <Icon name="refresh" size={15} />{start.isPending ? 'Запускаем…' : running && !staleJob ? 'Проверка идёт…' : report ? 'Обновить проверку' : 'Проверить каталог МС'}
      </button>
    </header>
    <p className="gtin-control__scope">Каталог общий для всех юрлиц аккаунта МС. Проверка учитывает GTIN товаров и упаковок; карточки и привязки не изменяются.</p>
    {visibleError && <div className="alert alert--error" role="alert">{errorText(visibleError)} <button type="button" className="button button--sm" onClick={() => void state.refetch()}>Повторить загрузку</button></div>}
    {running && <div className="gtin-control__progress" role="status" aria-live="polite">
      <Icon name="refresh" size={16} /><span>{phases[job!.phase] || 'Проверка выполняется'}.
        {job!.products_checked > 0 && ` Прочитано карточек: ${number(job!.products_checked)}.`}
        {report && ' Ниже показан предыдущий завершённый отчёт.'}
        {staleJob && ' Долго нет обновлений. Можно повторно запустить проверку.'}</span>
    </div>}
    {job?.status === 'error' && <div className="alert alert--error" role="alert">
      {job.error_message || 'Проверка не завершилась.'} {report && 'Показан предыдущий завершённый отчёт.'}
    </div>}
    {state.isLoading ? <div className="gtin-control__empty" role="status">Загружаем результаты проверки…</div>
      : !report ? <div className="gtin-control__empty">
        <Icon name="scan" size={32} /><h2>{running ? 'Проверяем каталог в фоне' : 'Каталог ещё не проверен'}</h2>
        <p>{running ? 'Можно продолжать работу в других разделах. Результат сохранится после завершения.'
          : 'Запустите проверку, чтобы найти устаревшие привязки, архивные карточки и повторяющиеся GTIN в МойСкладе.'}</p>
      </div> : <>
        <div className="gtin-control__summary">
          <span>Проверено: <strong>{date(report.finished_at)}</strong></span>
          <span>Привязок: <strong>{number(summary.mapping_count)}</strong></span>
          <span>Карточек МС: <strong>{number((summary.active_products || 0) + (summary.archived_products || 0))}</strong></span>
          <span>Без расхождений: <strong>{number(summary.matched)}</strong></span>
        </div>
        <section className="gtin-control__results" aria-label="Результаты контроля GTIN">
          <div className="gtin-control__tabs" role="tablist" aria-label="Вид проверки GTIN">
            <button type="button" id="gtin-mapping-tab" role="tab" aria-selected={tab === 'mapping'}
              aria-controls="gtin-results" onClick={() => selectTab('mapping')}>Расхождения привязок <span>{number(summary.mapping_findings)}</span></button>
            <button type="button" id="gtin-duplicate-tab" role="tab" aria-selected={tab === 'duplicate'}
              aria-controls="gtin-results" onClick={() => selectTab('duplicate')}>Дубли в МС <span>{number(archive ? summary.duplicate_findings : summary.duplicate_active)}</span></button>
          </div>
          <div className="gtin-control__filters">
            <label className="gtin-control__search">Поиск по GTIN, товару или ID
              <input type="search" className="ui-input" value={search} onChange={event => setSearch(event.target.value)} placeholder="GTIN или название товара" maxLength={200} />
            </label>
            {tab === 'mapping' ? <label>Тип расхождения<select className="ui-input" value={category} onChange={event => { setCategory(event.target.value); setPage(1) }}>
              <option value="">Все расхождения</option>
              {Object.entries(categories).filter(([key]) => !key.startsWith('duplicate')).map(([key, value]) =>
                <option key={key} value={key}>{value.label}</option>)}
            </select></label> : <label className="gtin-control__archive"><input type="checkbox" checked={archive}
              onChange={event => { setArchive(event.target.checked); setPage(1) }} />Учитывать архивные карточки</label>}
          </div>
          <p className="gtin-control__explanation">{tab === 'mapping'
            ? 'Отсутствие GTIN в карточке или её архивный статус сами по себе не доказывают ошибку. Проверьте назначение товара перед исправлением.'
            : 'Повтор кода внутри одной карточки не считается дублем. Здесь показаны GTIN, найденные у разных товаров; без фильтра архива — только у действующих.'}</p>
          <div id="gtin-results" role="tabpanel" aria-labelledby={tab === 'mapping' ? 'gtin-mapping-tab' : 'gtin-duplicate-tab'}>
            {findings.isLoading ? <div className="gtin-control__empty" role="status">Загружаем список…</div>
              : findings.isError ? <div className="gtin-control__empty" role="alert"><p>{errorText(findings.error)}</p><button type="button" className="button" onClick={() => void findings.refetch()}>Повторить загрузку</button></div>
              : !items.length ? <div className="gtin-control__empty"><h2>{query || category || (tab === 'duplicate' && !archive) ? 'Совпадений по фильтрам нет' : 'Расхождения не найдены'}</h2><p>Результат относится к проверке от {date(report.finished_at)}.</p></div>
              : <div className="gtin-control__table-wrap"><table className="gtin-control__table">
                <thead><tr><th scope="col">GTIN</th>{tab === 'mapping' && <th scope="col">Сохранено в Skandata</th>}
                  <th scope="col">Карточки с этим GTIN в МС</th><th scope="col">Результат проверки</th></tr></thead>
                <tbody>{items.map(item => <Finding key={item.id} item={item} mapping={tab === 'mapping'} runId={report.id} />)}</tbody>
              </table></div>}
          </div>
          {findings.data && <footer className="gtin-control__pagination">
            <span>Найдено: {number(total)}</span><div>
              <button type="button" className="button button--sm" disabled={page <= 1 || findings.isFetching} onClick={() => setPage(value => value - 1)}>Назад</button>
              <span>Страница {page} из {pages}</span>
              <button type="button" className="button button--sm" disabled={page >= pages || findings.isFetching} onClick={() => setPage(value => value + 1)}>Далее</button>
            </div>
          </footer>}
        </section>
      </>}
  </main>
}
