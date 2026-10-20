import type { CodeSearchHit, UpdSearchHit, MarkHistoryResponse } from '../api/client'
import { markChronology, chronologyDate } from '../lib/markChronology'

type Props = {
  scans: CodeSearchHit[]
  upds: UpdSearchHit[]
  history: MarkHistoryResponse | null
  loading: boolean
  documentsLoading?: boolean
  error: string | null
  onRefresh: () => void
}

export function MarkChronology({ scans, upds, history, loading, documentsLoading, error, onRefresh }: Props) {
  const entries = markChronology(scans, upds, history?.events || [])
  const current = history?.current
  const errors = [...new Set([error, history?.info_error, history?.history_error].filter(Boolean))]
  const busy = loading || Boolean(history?.refreshing)
  return <section className="mark-chronology" aria-label="Хронология марки" aria-busy={busy}>
    <div className="mark-chronology__head">
      <h2>Хронология марки</h2>
      <button type="button" className="button button--sm" disabled={busy} onClick={onRefresh}>
        {busy ? 'Обновление ЧЗ…' : 'Обновить ЧЗ'}
      </button>
    </div>
    <p className="text-muted">{history ? `Сведения ЧЗ: ${history.organization_name}. ` : ''}Даты и время — Москва.</p>
    {errors.map(message => <p key={message} className="alert alert--warn" role="status">{message}</p>)}
    <div className="mark-chronology__layout">
      <aside className="mark-chronology__summary" aria-label="Последнее состояние ЧЗ">
        <h3>Последнее полученное состояние</h3>
        <p className="text-muted">{history?.info_checked_at ? `Получено ${chronologyDate(history.info_checked_at)}` : busy ? 'Запрашиваем сведения ЧЗ…' : 'Ответ ЧЗ ещё не получен'}</p>
        {current?.product_name && <p>{current.product_name}</p>}
        <dl>
          <dt>Статус ЧЗ</dt><dd>{current?.status_label || 'Неизвестен'}</dd>
          <dt>{current?.owner_is_stale ? 'Последний известный владелец' : current?.withdrawn ? 'Указанный владелец (марка выбыла)' : 'Владелец в ответе ЧЗ'}</dt>
          <dd><span>{current?.owner_name || 'Не передан'}</span>{current?.owner_inn && <span className="text-muted mark-chronology__line">ИНН {current.owner_inn}</span>}
            {current?.owner_is_stale && <span className="text-muted mark-chronology__line">В новом ответе владелец не передан. Последние сведения: {chronologyDate(current.owner_checked_at || null)}.</span>}</dd>
          <dt>{current?.region_source === 'address' ? 'Регион по адресу места деятельности' : 'Регион'}</dt>
          <dd>{current?.region || 'Регион не передан ЧЗ'}</dd>
          <dt>Место деятельности владельца</dt><dd>{current?.owner_address || 'Адрес не передан ЧЗ'}</dd>
          <dt>Причина выбытия</dt><dd>{current?.withdraw_reason_label || 'Не передана'}{current?.withdraw_reason && <span className="text-muted mark-chronology__line">{current.withdraw_reason}</span>}</dd>
          {current?.withdrawn && <><dt>Дата конечного выбытия</dt><dd>Не установлена по доступным данным</dd></>}
        </dl>
        <p className="hint">Это состояние на дату проверки. Адрес места деятельности не подтверждает адрес кассы или место продажи.</p>
      </aside>
      <div className="mark-chronology__events">
        <p className="hint">События из наших документов и доступной истории ЧЗ. Время скана не является датой передачи товара.</p>
        {entries.length > 0 ? <ol className="mark-timeline">
          {entries.map(entry => <li key={entry.id}>
            <div className="mark-timeline__date"><time dateTime={entry.at || undefined}>{chronologyDate(entry.at, entry.source !== 'Скандата')}</time><span className="text-muted">{entry.dateLabel}</span></div>
            <div className="mark-timeline__content"><span className="badge badge--info">{entry.source}</span><h3>{entry.title}</h3><p>{entry.detail}</p>
              {entry.source === 'Честный Знак' && <details><summary>Реквизиты события ЧЗ</summary>
                <p>Документ: {entry.documentId || 'Не передан'}</p>
                <p>Зарегистрировано в ЧЗ: {chronologyDate(entry.recordedAt || null)}</p>
              </details>}
            </div>
          </li>)}
        </ol> : <p className="hint">{documentsLoading ? 'Ищем наши документы…' : 'Документы и события для этой марки пока не найдены.'}</p>}
        <div className="mark-chronology__gap"><strong>Дальнейшие переходы могут быть неизвестны</strong>
          <p>ЧЗ может не раскрывать промежуточные перепродажи. Полный маршрут между нашей передачей и последним состоянием не подтверждён.</p>
          {!history?.history_checked_at && !busy && <p>Доступная история ЧЗ не получена.</p>}
          {history?.history_checked_at && <p>История получена {chronologyDate(history.history_checked_at)}.{history.events?.length === 0 ? ' ЧЗ вернул пустой список событий; это не доказывает отсутствие движения.' : ''}</p>}
        </div>
      </div>
    </div>
  </section>
}
