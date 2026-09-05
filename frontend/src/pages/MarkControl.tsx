import { useEffect, useState } from 'react'
import {
  markControlApi,
  type SabyStatus,
  type EdoDocRow,
  type EdoSyncResult,
  type EdoDbDoc,
  type EdoStuckResult,
} from '../api/client'

/** Дата ДД.ММ.ГГГГ из Date. */
function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`
}

function isUnsigned(r: EdoDocRow): boolean {
  return Boolean(r.unsigned)
}

export function MarkControlPage() {
  const [status, setStatus] = useState<SabyStatus | null>(null)
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

  useEffect(() => {
    markControlApi.sabyStatus().then((r) => setStatus(r.data)).catch(() => setStatus({ connected: false }))
  }, [])

  const connect = async () => {
    setConnecting(true)
    setErr(null)
    try {
      const payload =
        authMode === 'service'
          ? {
              app_client_id: appClientId.trim(),
              app_secret: appSecret.trim() || undefined,
              secret_key: secretKey.trim() || undefined,
            }
          : { login: login.trim(), password, account: account.trim() || undefined }
      const r = await markControlApi.sabyConnect(payload)
      setStatus(r.data)
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
      const r = await markControlApi.sabyDocuments({
        direction: 'Исходящий',
        date_from: dateFrom,
        date_to: dateTo,
        page_size: 200,
      })
      setDocs(r.data.documents)
      setUnsigned(r.data.unsigned_count)
      setHasLoaded(true)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось получить документы Saby')
    } finally {
      setLoading(false)
    }
  }

  // Синхронизация ЭДО в БД (полный проход за период → марки)
  const [syncFrom, setSyncFrom] = useState(fmt(new Date(Date.now() - 90 * 24 * 3600 * 1000)))
  const [syncRunning, setSyncRunning] = useState(false)
  const [syncResult, setSyncResult] = useState<EdoSyncResult | null>(null)
  const [dbDocs, setDbDocs] = useState<EdoDbDoc[]>([])

  const [stuck, setStuck] = useState<EdoStuckResult | null>(null)
  const [stuckLoading, setStuckLoading] = useState(false)
  const [stuckView, setStuckView] = useState<'counterparties' | 'documents'>('counterparties')
  const [snapRunning, setSnapRunning] = useState(false)
  const [snapInfo, setSnapInfo] = useState<{ size: number; at: string | null } | null>(null)

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
      setSyncRunning(true)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось запустить синхронизацию')
    }
  }

  useEffect(() => {
    if (!syncRunning) return
    const t = setInterval(async () => {
      try {
        const r = await markControlApi.edoSyncStatus()
        if (r.data.result) setSyncResult(r.data.result)
        if (!r.data.running) {
          setSyncRunning(false)
          loadDbDocs()
          loadStuck()
        }
      } catch { /* ignore */ }
    }, 4000)
    return () => clearInterval(t)
  }, [syncRunning])

  useEffect(() => {
    if (status?.connected) {
      loadDbDocs()
      loadStuck()
      loadSnapStatus()
    }
  }, [status?.connected])

  // Опрос обновления снимка ЧЗ
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

  const shown = onlyUnsigned ? docs.filter(isUnsigned) : docs

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <h1 style={{ marginTop: 0 }}>Контроль марок</h1>
      <p style={{ color: '#666', marginTop: -8 }}>
        Отлов исходящих УПД, которые покупатель не принял/не подписал — марки «зависают» на
        продавце в ЧЗ. Данные из ЭДО Saby (СБИС).
      </p>

      {/* ГЛАВНОЕ: Не принятые УПД (сверка ЭДО с остатком ЧЗ) */}
      {status?.connected && (
        <section style={{ ...card, borderColor: '#f0c0c0', background: '#fffafa' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <h2 style={{ ...h2, marginBottom: 0 }}>⚠️ Не принятые УПД (марки зависли на нас)</h2>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={btnGhost} onClick={loadStuck} disabled={stuckLoading}>
                {stuckLoading ? 'Сверка…' : 'Пересверить'}
              </button>
              <button style={btnGhost} onClick={refreshSnapshot} disabled={snapRunning}>
                {snapRunning ? 'Обновление ЧЗ…' : 'Обновить остаток ЧЗ'}
              </button>
            </div>
          </div>
          <div style={{ color: '#8a8f98', fontSize: 13, margin: '8px 0 12px' }}>
            Не принято = документ не утверждён покупателем в ЭДО (Доставлен/Отправлен/
            Приглашение/Проблемы), кроме аннулированных. «Марок за нами» — сколько кодов
            этих УПД ещё числится на нас в ЧЗ (физически зависли).
            {snapInfo && (
              <> Остаток ЧЗ: <b>{snapInfo.size.toLocaleString('ru')}</b> марок
              {snapInfo.at ? `, от ${new Date(snapInfo.at).toLocaleString('ru')}` : ''}.</>
            )}
            {snapRunning && ' Идёт обновление остатка ЧЗ (несколько минут)…'}
          </div>
          {!stuck ? (
            <div style={{ color: '#888' }}>{stuckLoading ? 'Сверка…' : ''}</div>
          ) : !stuck.has_snapshot ? (
            <div style={{ color: '#b26a00' }}>
              Нет снимка остатка ЧЗ для сверки. Загрузите выгрузку ЧЗ (237k марок) — тогда
              появится список не принятых УПД.
            </div>
          ) : stuck.documents.length === 0 ? (
            <div style={{ color: '#2e7d32' }}>
              Не принятых УПД не найдено (снимок ЧЗ: {stuck.snapshot_size.toLocaleString('ru')} марок).
            </div>
          ) : (
            <>
              <div style={{ marginBottom: 10, color: '#444' }}>
                Контрагентов: <b style={{ color: '#c0392b' }}>{stuck.counterparties?.length ?? 0}</b> ·
                не принятых УПД: <b style={{ color: '#c0392b' }}>{stuck.stuck_docs}</b> ·
                из них марок ещё за нами: <b style={{ color: '#c0392b' }}>{stuck.stuck_marks?.toLocaleString('ru')}</b>
              </div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
                <button style={stuckView === 'counterparties' ? tabActive : tab} onClick={() => setStuckView('counterparties')}>
                  По контрагентам
                </button>
                <button style={stuckView === 'documents' ? tabActive : tab} onClick={() => setStuckView('documents')}>
                  По документам
                </button>
              </div>

              {stuckView === 'counterparties' ? (
                <div style={{ overflowX: 'auto' }}>
                  <table style={table}>
                    <thead>
                      <tr>
                        <th style={th}>Контрагент</th>
                        <th style={th}>ИНН</th>
                        <th style={th}>Не принято УПД</th>
                        <th style={th}>Марок всего</th>
                        <th style={th}>Марок за нами</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(stuck.counterparties ?? []).map((c, i) => (
                        <tr key={(c.counterparty_inn || '') + i}>
                          <td style={{ ...td, fontWeight: 500 }}>{c.counterparty_name || '—'}</td>
                          <td style={td}>{c.counterparty_inn || '—'}</td>
                          <td style={{ ...td, fontWeight: 700, color: '#c0392b' }}>{c.not_accepted_upd}</td>
                          <td style={{ ...td, color: '#888' }}>{c.marks_total.toLocaleString('ru')}</td>
                          <td style={{ ...td, color: '#c0392b' }}>{c.stuck_marks.toLocaleString('ru')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={table}>
                    <thead>
                      <tr>
                        <th style={th}>УПД №</th>
                        <th style={th}>Дата</th>
                        <th style={th}>Покупатель</th>
                        <th style={th}>ИНН</th>
                        <th style={th}>Статус ЭДО</th>
                        <th style={th}>Зависло / всего</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stuck.documents.map((d, i) => (
                        <tr key={(d.number || '') + i} style={{ background: '#fff5f5' }}>
                          <td style={td}>{d.number || '—'}</td>
                          <td style={td}>{d.doc_date || '—'}</td>
                          <td style={td}>{d.counterparty_name || '—'}</td>
                          <td style={td}>{d.counterparty_inn || '—'}</td>
                          <td style={td}>{d.state_name || '—'}</td>
                          <td style={{ ...td, fontWeight: 600, color: '#c0392b' }}>
                            {d.stuck} / {d.total}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </section>
      )}

      {/* Подключение Saby */}
      <section style={card}>
        <h2 style={h2}>Подключение Saby (ЭДО)</h2>
        {status?.connected ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span style={badgeOk}>● Подключено</span>
            <span style={{ color: '#444' }}>
              {status.mode === 'service' ? (
                <>Сервисная авторизация · ID подключения: <b>{status.app_client_id}</b></>
              ) : (
                <>Логин: <b>{status.login}</b>{status.account ? ` · Аккаунт: ${status.account}` : ''}</>
              )}
            </span>
            <button style={btnGhost} onClick={() => setStatus({ connected: false })}>
              Изменить доступ
            </button>
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <button
                style={authMode === 'service' ? tabActive : tab}
                onClick={() => setAuthMode('service')}
              >
                Сервисная (ключи приложения)
              </button>
              <button
                style={authMode === 'login' ? tabActive : tab}
                onClick={() => setAuthMode('login')}
              >
                Логин / пароль
              </button>
            </div>
            {authMode === 'service' ? (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: 8, alignItems: 'end' }}>
                <Field label="ID подключения (app_client_id)">
                  <input style={inp} value={appClientId} onChange={(e) => setAppClientId(e.target.value)} />
                </Field>
                <Field label="Защитный ключ">
                  <input style={inp} type="password" value={appSecret} onChange={(e) => setAppSecret(e.target.value)} />
                </Field>
                <Field label="Секретный ключ (если есть)">
                  <input style={inp} type="password" value={secretKey} onChange={(e) => setSecretKey(e.target.value)} />
                </Field>
                <button style={btn} disabled={connecting || !appClientId} onClick={connect}>
                  {connecting ? 'Проверка…' : 'Подключить'}
                </button>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: 8, alignItems: 'end' }}>
                <Field label="Логин Saby">
                  <input style={inp} value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" />
                </Field>
                <Field label="Пароль">
                  <input style={inp} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
                </Field>
                <Field label="Номер аккаунта (опц.)">
                  <input style={inp} value={account} onChange={(e) => setAccount(e.target.value)} />
                </Field>
                <button style={btn} disabled={connecting || !login || !password} onClick={connect}>
                  {connecting ? 'Проверка…' : 'Подключить'}
                </button>
              </div>
            )}
          </>
        )}
      </section>

      {/* Исходящие УПД */}
      {status?.connected && (
        <section style={card}>
          <h2 style={h2}>Исходящие УПД</h2>
          <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap', marginBottom: 12 }}>
            <Field label="Дата с (ДД.ММ.ГГГГ)">
              <input style={{ ...inp, width: 130 }} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            </Field>
            <Field label="Дата по">
              <input style={{ ...inp, width: 130 }} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
            </Field>
            <button style={btn} disabled={loading} onClick={load}>
              {loading ? 'Загрузка…' : 'Показать'}
            </button>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#444' }}>
              <input type="checkbox" checked={onlyUnsigned} onChange={(e) => setOnlyUnsigned(e.target.checked)} />
              Только не принятые покупателем
            </label>
          </div>

          <div style={{ marginBottom: 6, color: '#8a8f98', fontSize: 12 }}>
            Быстрый просмотр: только первая страница ленты изменений (≈25 событий с начала
            периода) — не все документы. Для полного охвата за период используйте блок
            «Синхронизация ЭДО в базу» ниже.
          </div>
          {hasLoaded && (
            <div style={{ marginBottom: 10, color: '#444' }}>
              Загружено реализаций: <b>{docs.length}</b> · Не принято покупателем:{' '}
              <b style={{ color: unsigned ? '#c0392b' : '#2e7d32' }}>{unsigned}</b>
              {onlyUnsigned && unsigned === 0 && docs.length > 0
                ? ' — все загруженные документы приняты/завершены'
                : ''}
            </div>
          )}

          {shown.length === 0 ? (
            <div style={{ color: '#888', padding: '16px 0' }}>
              {loading
                ? 'Загрузка…'
                : !hasLoaded
                ? 'Задайте период и нажмите «Показать».'
                : docs.length === 0
                ? 'За период документов не найдено.'
                : 'Нет не принятых покупателем — снимите галку, чтобы увидеть все.'}
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={table}>
                <thead>
                  <tr>
                    <th style={th}>Номер</th>
                    <th style={th}>Дата</th>
                    <th style={th}>Покупатель</th>
                    <th style={th}>ИНН</th>
                    <th style={th}>Статус Saby</th>
                    <th style={th}>Принят?</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((d, i) => {
                    const uns = isUnsigned(d)
                    return (
                      <tr key={d.id || i} style={{ background: uns ? '#fff5f5' : undefined }}>
                        <td style={td}>{d.number || '—'}</td>
                        <td style={td}>{d.date || '—'}</td>
                        <td style={td}>{d.counterparty_name || '—'}</td>
                        <td style={td}>{d.counterparty_inn || '—'}</td>
                        <td style={td}>
                          {d.state_name || (d.state_code != null ? `код ${d.state_code}` : '—')}
                          {d.state_desc ? <div style={{ color: '#888', fontSize: 12 }}>{d.state_desc}</div> : null}
                        </td>
                        <td style={{ ...td, fontWeight: 600, color: uns ? '#c0392b' : '#2e7d32' }}>
                          {uns ? 'Не принят' : 'Принят'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Синхронизация ЭДО в базу (полный проход за период → марки) */}
      {status?.connected && (
        <section style={card}>
          <h2 style={h2}>Синхронизация ЭДО в базу (для сверки с ЧЗ)</h2>
          <div style={{ color: '#8a8f98', fontSize: 13, marginBottom: 12 }}>
            Полный проход по ленте изменений Saby за период: скачиваем исходящие УПД,
            извлекаем коды маркировки в базу. Затем их можно сверить с остатком ЧЗ и найти
            марки, зависшие на продавце (контрагент не принял).
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap', marginBottom: 12 }}>
            <Field label="Синхронизировать с даты (ДД.ММ.ГГГГ)">
              <input style={{ ...inp, width: 150 }} value={syncFrom} onChange={(e) => setSyncFrom(e.target.value)} />
            </Field>
            <button style={btn} disabled={syncRunning} onClick={startSync}>
              {syncRunning ? 'Синхронизация…' : 'Синхронизировать'}
            </button>
            <button style={btnGhost} onClick={loadDbDocs}>Обновить таблицу</button>
          </div>

          {syncResult && (
            <div style={{ marginBottom: 10, color: '#444' }}>
              Обработано страниц: <b>{syncResult.pages}</b> · документов: <b>{syncResult.documents}</b> ·
              реализаций: <b>{syncResult.out_realizations}</b> · с марками: <b>{syncResult.parsed_docs}</b> ·
              кодов сохранено: <b style={{ color: '#1e63d6' }}>{syncResult.marks_saved}</b>
            </div>
          )}

          {dbDocs.length > 0 ? (
            <div style={{ overflowX: 'auto' }}>
              <table style={table}>
                <thead>
                  <tr>
                    <th style={th}>Номер</th>
                    <th style={th}>Дата</th>
                    <th style={th}>Покупатель</th>
                    <th style={th}>ИНН</th>
                    <th style={th}>Статус</th>
                    <th style={th}>Кодов</th>
                  </tr>
                </thead>
                <tbody>
                  {dbDocs.map((d, i) => (
                    <tr key={(d.number || '') + i}>
                      <td style={td}>{d.number || '—'}</td>
                      <td style={td}>{d.doc_date || '—'}</td>
                      <td style={td}>{d.counterparty_name || '—'}</td>
                      <td style={td}>{d.counterparty_inn || '—'}</td>
                      <td style={td}>{d.state_name || '—'}</td>
                      <td style={{ ...td, textAlign: 'right' }}>
                        {d.marks_parsed ? d.codes_total : '…'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ color: '#888' }}>
              База пуста — запустите синхронизацию за нужный период.
            </div>
          )}
        </section>
      )}

      {err && <div style={errBox}>{err}</div>}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13, color: '#555' }}>
      {label}
      {children}
    </label>
  )
}

const card: React.CSSProperties = { border: '1px solid #e5e5e5', borderRadius: 10, padding: 18, marginTop: 16, background: '#fff' }
const h2: React.CSSProperties = { margin: '0 0 14px', fontSize: 17 }
const inp: React.CSSProperties = { padding: '8px 10px', border: '1px solid #ccc', borderRadius: 6, fontSize: 14, width: '100%' }
const btn: React.CSSProperties = { padding: '9px 16px', background: '#1e63d6', color: '#fff', border: 0, borderRadius: 6, cursor: 'pointer', fontSize: 14 }
const btnGhost: React.CSSProperties = { padding: '7px 12px', background: 'transparent', color: '#1e63d6', border: '1px solid #1e63d6', borderRadius: 6, cursor: 'pointer', fontSize: 13 }
const badgeOk: React.CSSProperties = { color: '#2e7d32', fontWeight: 600 }
const tab: React.CSSProperties = { padding: '7px 12px', background: '#f2f4f7', color: '#444', border: '1px solid #dde1e6', borderRadius: 6, cursor: 'pointer', fontSize: 13 }
const tabActive: React.CSSProperties = { ...tab, background: '#1e63d6', color: '#fff', borderColor: '#1e63d6' }
const table: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 13 }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', borderBottom: '2px solid #eee', color: '#666', fontWeight: 600, whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid #f0f0f0' }
const errBox: React.CSSProperties = { marginTop: 14, padding: '10px 14px', background: '#fdecea', color: '#b71c1c', borderRadius: 6 }
