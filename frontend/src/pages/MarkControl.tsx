import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import {
  markControlApi,
  type SabyStatus,
  type EdoDocRow,
  type EdoSyncResult,
  type EdoSyncProgress,
  type EdoDbDoc,
  type EdoStuckResult,
} from '../api/client'

// Глубина синка/backfill: подпись → дней.
const DEPTH_OPTIONS: { label: string; days: number }[] = [
  { label: '3 месяца', days: 90 },
  { label: 'полгода', days: 180 },
  { label: 'год', days: 365 },
  { label: '2 года', days: 730 },
  { label: '3 года', days: 1095 },
]

/** Дата ДД.ММ.ГГГГ из Date. */
function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`
}

const nf = (n: number) => n.toLocaleString('ru')
const isUnsigned = (r: EdoDocRow): boolean => Boolean(r.unsigned)

export function MarkControlPage() {
  const [status, setStatus] = useState<SabyStatus | null>(null)
  const [editConn, setEditConn] = useState(false)
  const [authMode, setAuthMode] = useState<'service' | 'login'>('service')
  const [appClientId, setAppClientId] = useState('')
  const [appSecret, setAppSecret] = useState('')
  const [secretKey, setSecretKey] = useState('')
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [account, setAccount] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const today = new Date()
  const monthAgo = new Date(Date.now() - 30 * 24 * 3600 * 1000)
  const [dateFrom, setDateFrom] = useState(fmt(monthAgo))
  const [dateTo, setDateTo] = useState(fmt(today))
  const [loading, setLoading] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [docs, setDocs] = useState<EdoDocRow[]>([])
  const [unsigned, setUnsigned] = useState(0)
  const [onlyUnsigned, setOnlyUnsigned] = useState(false)

  const [syncFrom, setSyncFrom] = useState(fmt(new Date(Date.now() - 90 * 24 * 3600 * 1000)))
  const [syncRunning, setSyncRunning] = useState(false)
  const [syncResult, setSyncResult] = useState<EdoSyncResult | null>(null)
  const [syncProgress, setSyncProgress] = useState<EdoSyncProgress | null>(null)
  const [backfillDays, setBackfillDays] = useState(365)
  const [dbDocs, setDbDocs] = useState<EdoDbDoc[]>([])

  const [stuck, setStuck] = useState<EdoStuckResult | null>(null)
  const [stuckLoading, setStuckLoading] = useState(false)
  const [stuckView, setStuckView] = useState<'counterparties' | 'documents'>('counterparties')
  const [snapRunning, setSnapRunning] = useState(false)
  const [snapInfo, setSnapInfo] = useState<{ size: number; at: string | null } | null>(null)

  useEffect(() => {
    markControlApi.sabyStatus().then((r) => setStatus(r.data)).catch(() => setStatus({ connected: false }))
  }, [])

  const connect = async () => {
    setConnecting(true)
    setErr(null)
    try {
      const payload =
        authMode === 'service'
          ? { app_client_id: appClientId.trim(), app_secret: appSecret.trim() || undefined, secret_key: secretKey.trim() || undefined }
          : { login: login.trim(), password, account: account.trim() || undefined }
      const r = await markControlApi.sabyConnect(payload)
      setStatus(r.data)
      setEditConn(false)
      setPassword('')
      setAppSecret('')
      setSecretKey('')
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось подключить Saby')
    } finally {
      setConnecting(false)
    }
  }

  const load = async () => {
    setLoading(true)
    setErr(null)
    try {
      const r = await markControlApi.sabyDocuments({ direction: 'Исходящий', date_from: dateFrom, date_to: dateTo, page_size: 200 })
      setDocs(r.data.documents)
      setUnsigned(r.data.unsigned_count)
      setHasLoaded(true)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось получить документы Saby')
    } finally {
      setLoading(false)
    }
  }

  const loadSnapStatus = async () => {
    try {
      const r = await markControlApi.czSnapshotStatus()
      setSnapRunning(r.data.running)
      setSnapInfo({ size: r.data.size, at: r.data.at })
      return r.data.running
    } catch {
      return false
    }
  }

  const refreshSnapshot = async () => {
    setErr(null)
    try {
      await markControlApi.czSnapshotRefresh()
      setSnapRunning(true)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось запустить обновление остатка ЧЗ')
    }
  }

  const loadDbDocs = async () => {
    try {
      const r = await markControlApi.edoDocumentsDb()
      setDbDocs(r.data)
    } catch { /* ignore */ }
  }

  const loadStuck = async () => {
    setStuckLoading(true)
    try {
      const r = await markControlApi.edoStuck()
      setStuck(r.data)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось сверить с ЧЗ')
    } finally {
      setStuckLoading(false)
    }
  }

  const startSync = async () => {
    setErr(null)
    try {
      await markControlApi.edoSync(syncFrom)
      setSyncProgress(null)
      setSyncRunning(true)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось запустить синхронизацию')
    }
  }

  const startBackfillNames = async () => {
    setErr(null)
    try {
      await markControlApi.edoBackfillNames(backfillDays)
      setSyncProgress(null)
      setSyncRunning(true)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось запустить заполнение имён')
    }
  }

  useEffect(() => {
    if (!syncRunning) return
    const t = setInterval(async () => {
      try {
        const r = await markControlApi.edoSyncStatus()
        if (r.data.result) setSyncResult(r.data.result)
        setSyncProgress(r.data.progress)
        if (!r.data.running) {
          setSyncRunning(false)
          setSyncProgress(null)
          loadDbDocs()
          loadStuck()
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(t)
  }, [syncRunning])

  useEffect(() => {
    if (status?.connected) {
      loadDbDocs()
      loadStuck()
      loadSnapStatus()
    }
  }, [status?.connected])

  useEffect(() => {
    if (!snapRunning) return
    const t = setInterval(async () => {
      const running = await loadSnapStatus()
      if (!running) {
        clearInterval(t)
        loadStuck()
      }
    }, 5000)
    return () => clearInterval(t)
  }, [snapRunning])

  const exportXlsx = async () => {
    try {
      const r = await markControlApi.edoStuckXlsx()
      const url = URL.createObjectURL(r.data as Blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `Не принятые УПД ${fmt(new Date())}.xlsx`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось выгрузить XLSX')
    }
  }

  const shown = onlyUnsigned ? docs.filter(isUnsigned) : docs
  const connected = Boolean(status?.connected)
  const hasStuck = Boolean(stuck?.has_snapshot && (stuck?.documents.length ?? 0) > 0)

  return (
    <div className="mc">
      <header className="mc-head">
        <div>
          <h1 className="mc-head__title">Контроль марок</h1>
          <p className="mc-head__sub">
            Исходящие УПД, которые контрагент не принял, и марки, зависшие на вас
            в Честном Знаке. Данные из ЭДО Saby (СБИС).
          </p>
        </div>
        {connected && (
          <div className="mc-head__actions">
            {hasStuck && (
              <button className="button" onClick={exportXlsx}>
                <Icon name="upload" size={15} />
                Экспорт XLSX
              </button>
            )}
            <button className="button" onClick={loadStuck} disabled={stuckLoading}>
              <Icon name="refresh" size={15} />
              {stuckLoading ? 'Сверка…' : 'Пересверить'}
            </button>
            <button className="button button--primary" onClick={refreshSnapshot} disabled={snapRunning}>
              {snapRunning ? 'Обновление ЧЗ…' : 'Обновить остаток ЧЗ'}
            </button>
          </div>
        )}
      </header>

      {connected && (
        <div className="mc-meta">
          <span className="mc-meta__item">
            <span className="badge badge--ok"><span className="badge__dot" />Saby подключён</span>
          </span>
          <span className="mc-meta__sep" />
          <span className="mc-meta__item">
            Остаток ЧЗ:&nbsp;<b className="tabular">{snapInfo ? nf(snapInfo.size) : '—'}</b>&nbsp;марок
            {snapInfo?.at ? ` · от ${new Date(snapInfo.at).toLocaleDateString('ru')}` : ''}
          </span>
          {snapRunning && (
            <>
              <span className="mc-meta__sep" />
              <span className="mc-meta__item text-muted">обновление остатка ЧЗ…</span>
            </>
          )}
        </div>
      )}

      {err && (
        <div className="alert alert--error" style={{ marginTop: 16 }}>
          {err}
          <span className="alert__spacer" />
          <button className="button button--sm" onClick={() => setErr(null)}>Закрыть</button>
        </div>
      )}

      {/* Не подключено → подключение как основная задача */}
      {!connected && (
        <section className="card" style={{ marginTop: 18 }}>
          <h2 className="mc-tool__title">Подключение Saby (ЭДО)</h2>
          <p className="mc-tool__desc">
            Укажите доступ к ЭДО Saby — после подключения появится отчёт по не принятым УПД.
          </p>
          {renderConnectForm()}
        </section>
      )}

      {/* Отчёт — главный блок */}
      {connected && (
        <section className="mc-report">
          <div className="mc-report__bar">
            <h2 className="mc-report__title">Не принятые УПД</h2>
            {stuck?.has_snapshot && (stuck.documents.length > 0) && (
              <div className="seg" role="tablist">
                <button
                  className={`seg__btn${stuckView === 'counterparties' ? ' is-active' : ''}`}
                  onClick={() => setStuckView('counterparties')}
                >
                  По контрагентам
                </button>
                <button
                  className={`seg__btn${stuckView === 'documents' ? ' is-active' : ''}`}
                  onClick={() => setStuckView('documents')}
                >
                  По документам
                </button>
              </div>
            )}
          </div>

          {!stuck ? (
            <div className="mc-empty">{stuckLoading ? 'Сверка с Честным Знаком…' : 'Нет данных.'}</div>
          ) : !stuck.has_snapshot ? (
            <div style={{ padding: '0 20px 20px' }}>
              <div className="alert alert--warn">
                Нет снимка остатка ЧЗ для сверки. Нажмите «Обновить остаток ЧЗ» — после выгрузки
                появится список не принятых УПД.
              </div>
            </div>
          ) : stuck.documents.length === 0 ? (
            <div className="mc-empty">
              Не принятых УПД не найдено. Снимок ЧЗ: {nf(stuck.snapshot_size)} марок.
            </div>
          ) : (
            <>
              <div className="mc-stats">
                <div className="mc-stat">
                  <div className="mc-stat__num">{nf(stuck.counterparties?.length ?? 0)}</div>
                  <div className="mc-stat__label">контрагентов</div>
                </div>
                <div className="mc-stat">
                  <div className="mc-stat__num mc-stat__num--alert">{nf(stuck.stuck_docs ?? 0)}</div>
                  <div className="mc-stat__label">не принятых УПД</div>
                </div>
                <div className="mc-stat">
                  <div className="mc-stat__num mc-stat__num--alert">{nf(stuck.stuck_marks ?? 0)}</div>
                  <div className="mc-stat__label">марок ещё за нами</div>
                </div>
              </div>

              <div className="mc-table-wrap">
                {stuckView === 'counterparties' ? (
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>Контрагент</th>
                        <th>ИНН</th>
                        <th className="mc-num">Не принято УПД</th>
                        <th className="mc-num">Марок всего</th>
                        <th className="mc-num">Марок за нами</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(stuck.counterparties ?? []).map((c, i) => (
                        <tr key={(c.counterparty_inn || '') + i}>
                          <td style={{ fontWeight: 500 }}>{c.counterparty_name || '—'}</td>
                          <td className="mc-inn">{c.counterparty_inn || '—'}</td>
                          <td className="mc-num--alert">{nf(c.not_accepted_upd)}</td>
                          <td className="mc-num">{nf(c.marks_total)}</td>
                          <td className="mc-num">{nf(c.stuck_marks)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>УПД №</th>
                        <th>Дата</th>
                        <th>Покупатель</th>
                        <th>ИНН</th>
                        <th>Статус ЭДО</th>
                        <th className="mc-num">Зависло / всего</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stuck.documents.map((d, i) => (
                        <tr key={(d.number || '') + i}>
                          <td style={{ fontWeight: 500 }}>{d.number || '—'}</td>
                          <td className="tabular">{d.doc_date || '—'}</td>
                          <td>{d.counterparty_name || '—'}</td>
                          <td className="mc-inn">{d.counterparty_inn || '—'}</td>
                          <td className="mc-state">{d.state_name || '—'}</td>
                          <td className="mc-num--alert">{nf(d.stuck)} / {nf(d.total)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </>
          )}
        </section>
      )}

      {/* Настройка и синхронизация — второстепенное, спрятано */}
      {connected && (
        <details className="mc-tools">
          <summary>
            <Icon name="chevron" size={16} className="mc-tools__chev" />
            Настройка и синхронизация
          </summary>
          <div className="mc-tools__body">
            {/* Подключение Saby */}
            <div className="mc-tool">
              <h3 className="mc-tool__title">Подключение Saby (ЭДО)</h3>
              {!editConn ? (
                <div className="flex-row gap-8" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
                  <span className="text-muted">
                    {status?.mode === 'service'
                      ? <>Сервисная авторизация · ID подключения <b>{status.app_client_id}</b></>
                      : <>Логин <b>{status?.login}</b>{status?.account ? ` · аккаунт ${status.account}` : ''}</>}
                  </span>
                  <button className="button button--sm" onClick={() => setEditConn(true)}>Изменить доступ</button>
                </div>
              ) : (
                renderConnectForm(true)
              )}
            </div>

            {/* Синхронизация ЭДО */}
            <div className="mc-tool">
              <h3 className="mc-tool__title">Синхронизация ЭДО в базу</h3>
              <p className="mc-tool__desc">
                Полный проход по ленте изменений Saby за период: скачиваем исходящие УПД и
                извлекаем коды маркировки в базу для сверки с остатком ЧЗ.
              </p>
              <div className="mc-form">
                <div className="mc-form__field">
                  <label className="field-label">Синхронизировать с даты</label>
                  <input className="ui-input" value={syncFrom} onChange={(e) => setSyncFrom(e.target.value)} placeholder="ДД.ММ.ГГГГ" />
                </div>
                <button className="button button--primary" disabled={syncRunning} onClick={startSync}>
                  {syncRunning ? 'Синхронизация…' : 'Синхронизировать'}
                </button>
                <button className="button" onClick={loadDbDocs}>Обновить таблицу</button>
              </div>

              <div className="mc-form" style={{ marginTop: 8, alignItems: 'flex-end' }}>
                <div className="mc-form__field" style={{ minWidth: 150 }}>
                  <label className="field-label">Глубина «Заполнить имена»</label>
                  <select className="ui-input" value={backfillDays} disabled={syncRunning}
                    onChange={(e) => setBackfillDays(Number(e.target.value))}>
                    {DEPTH_OPTIONS.map((o) => (
                      <option key={o.days} value={o.days}>{o.label}</option>
                    ))}
                  </select>
                </div>
                <button className="button" disabled={syncRunning} onClick={startBackfillNames}
                  title="Разово докачать УПД по истории и заполнить наименования по GTIN для «Инвентаризации» (марки не трогает)">
                  {syncRunning ? 'Выполняется…' : 'Заполнить имена из истории'}
                </button>
              </div>

              {syncRunning && (
                <div className="mc-run-result" style={{ marginTop: 10 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <span>
                      {syncProgress?.backfill ? 'Заполнение имён из истории' : 'Синхронизация'}
                      {syncProgress ? <> · страниц: <b>{nf(syncProgress.pages)}</b> · реализаций: <b>{nf(syncProgress.out_realizations)}</b>
                        {syncProgress.backfill
                          ? <> · имён: <b style={{ color: 'var(--brand-strong)' }}>{nf(syncProgress.names_saved)}</b></>
                          : <> · кодов: <b style={{ color: 'var(--brand-strong)' }}>{nf(syncProgress.marks_saved)}</b></>}
                      </> : ' запускается…'}
                    </span>
                    <span>{syncProgress?.percent != null ? `${syncProgress.percent}%` : ''}</span>
                  </div>
                  <div className="mc-progress">
                    <div
                      className={`mc-progress__bar${syncProgress?.percent == null ? ' mc-progress__bar--indeterminate' : ''}`}
                      style={syncProgress?.percent != null ? { width: `${syncProgress.percent}%` } : undefined}
                    />
                  </div>
                </div>
              )}

              {!syncRunning && syncResult && (
                <div className="mc-run-result">
                  Страниц: <b>{nf(syncResult.pages)}</b> · документов: <b>{nf(syncResult.documents)}</b> ·
                  реализаций: <b>{nf(syncResult.out_realizations)}</b> · с марками: <b>{nf(syncResult.parsed_docs)}</b> ·
                  кодов сохранено: <b style={{ color: 'var(--brand-strong)' }}>{nf(syncResult.marks_saved)}</b>
                  {syncResult.names_saved != null && (
                    <> · имён по GTIN: <b style={{ color: 'var(--brand-strong)' }}>{nf(syncResult.names_saved)}</b></>
                  )}
                </div>
              )}
              {dbDocs.length > 0 && (
                <div className="mc-run-result">В базе реализаций с марками: <b>{nf(dbDocs.length)}</b></div>
              )}
            </div>

            {/* Быстрый просмотр ленты */}
            <div className="mc-tool">
              <h3 className="mc-tool__title">Быстрый просмотр ленты</h3>
              <p className="mc-tool__desc">
                Первая страница ленты изменений Saby за период (≈25 событий с начала периода) —
                для проверки связи, не полный список.
              </p>
              <div className="mc-form">
                <div className="mc-form__field" style={{ minWidth: 130 }}>
                  <label className="field-label">Дата с</label>
                  <input className="ui-input" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} placeholder="ДД.ММ.ГГГГ" />
                </div>
                <div className="mc-form__field" style={{ minWidth: 130 }}>
                  <label className="field-label">Дата по</label>
                  <input className="ui-input" value={dateTo} onChange={(e) => setDateTo(e.target.value)} placeholder="ДД.ММ.ГГГГ" />
                </div>
                <button className="button" disabled={loading} onClick={load}>
                  {loading ? 'Загрузка…' : 'Показать'}
                </button>
                <label className="flex-row gap-8" style={{ alignItems: 'center' }}>
                  <input type="checkbox" checked={onlyUnsigned} onChange={(e) => setOnlyUnsigned(e.target.checked)} />
                  <span className="text-muted">Только не принятые</span>
                </label>
              </div>
              {hasLoaded && (
                <div className="mc-run-result">
                  Загружено реализаций: <b>{nf(docs.length)}</b> · не принято:{' '}
                  <b style={{ color: unsigned ? 'var(--st-err-fg)' : 'var(--st-ok-fg)' }}>{nf(unsigned)}</b>
                </div>
              )}
              {shown.length > 0 && (
                <div className="mc-table-wrap" style={{ marginTop: 12, border: '1px solid var(--ms-border-light)', borderRadius: 'var(--r-md)' }}>
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>Номер</th><th>Дата</th><th>Покупатель</th><th>ИНН</th><th>Статус Saby</th><th>Принят?</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((d, i) => {
                        const uns = isUnsigned(d)
                        return (
                          <tr key={d.id || i}>
                            <td>{d.number || '—'}</td>
                            <td className="tabular">{d.date || '—'}</td>
                            <td>{d.counterparty_name || '—'}</td>
                            <td className="mc-inn">{d.counterparty_inn || '—'}</td>
                            <td className="mc-state">
                              {d.state_name || (d.state_code != null ? `код ${d.state_code}` : '—')}
                              {d.state_desc ? <div style={{ color: 'var(--ms-text-subtle)', fontSize: 12 }}>{d.state_desc}</div> : null}
                            </td>
                            <td>
                              <span className={`badge ${uns ? 'badge--warn' : 'badge--ok'}`}>
                                <span className="badge__dot" />{uns ? 'Не принят' : 'Принят'}
                              </span>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </details>
      )}
    </div>
  )

  function renderConnectForm(inTools = false) {
    return (
      <>
        <div className="seg" style={{ marginBottom: 14 }}>
          <button className={`seg__btn${authMode === 'service' ? ' is-active' : ''}`} onClick={() => setAuthMode('service')}>
            Сервисная (ключи приложения)
          </button>
          <button className={`seg__btn${authMode === 'login' ? ' is-active' : ''}`} onClick={() => setAuthMode('login')}>
            Логин / пароль
          </button>
        </div>
        <div className="mc-form">
          {authMode === 'service' ? (
            <>
              <div className="mc-form__field" style={{ minWidth: 220 }}>
                <label className="field-label">ID подключения (app_client_id)</label>
                <input className="ui-input" value={appClientId} onChange={(e) => setAppClientId(e.target.value)} />
              </div>
              <div className="mc-form__field">
                <label className="field-label">Защитный ключ</label>
                <input className="ui-input" type="password" value={appSecret} onChange={(e) => setAppSecret(e.target.value)} />
              </div>
              <div className="mc-form__field">
                <label className="field-label">Секретный ключ (если есть)</label>
                <input className="ui-input" type="password" value={secretKey} onChange={(e) => setSecretKey(e.target.value)} />
              </div>
              <button className="button button--primary" disabled={connecting || !appClientId} onClick={connect}>
                {connecting ? 'Проверка…' : 'Подключить'}
              </button>
            </>
          ) : (
            <>
              <div className="mc-form__field">
                <label className="field-label">Логин Saby</label>
                <input className="ui-input" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" />
              </div>
              <div className="mc-form__field">
                <label className="field-label">Пароль</label>
                <input className="ui-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
              </div>
              <div className="mc-form__field">
                <label className="field-label">Номер аккаунта (опц.)</label>
                <input className="ui-input" value={account} onChange={(e) => setAccount(e.target.value)} />
              </div>
              <button className="button button--primary" disabled={connecting || !login || !password} onClick={connect}>
                {connecting ? 'Проверка…' : 'Подключить'}
              </button>
            </>
          )}
          {inTools && (
            <button className="button" onClick={() => setEditConn(false)}>Отмена</button>
          )}
        </div>
      </>
    )
  }
}
