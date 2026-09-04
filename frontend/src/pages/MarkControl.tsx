import { useEffect, useState } from 'react'
import {
  markControlApi,
  type SabyStatus,
  type EdoDocRow,
} from '../api/client'

/** Дата ДД.ММ.ГГГГ из Date. */
function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`
}

function isUnsigned(r: EdoDocRow): boolean {
  return Boolean(r.incomplete) || r.state_code === 23
}

export function MarkControlPage() {
  const [status, setStatus] = useState<SabyStatus | null>(null)
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
  const [docs, setDocs] = useState<EdoDocRow[]>([])
  const [unsigned, setUnsigned] = useState(0)
  const [onlyUnsigned, setOnlyUnsigned] = useState(true)

  useEffect(() => {
    markControlApi.sabyStatus().then((r) => setStatus(r.data)).catch(() => setStatus({ connected: false }))
  }, [])

  const connect = async () => {
    setConnecting(true)
    setErr(null)
    try {
      const r = await markControlApi.sabyConnect(login.trim(), password, account.trim() || undefined)
      setStatus(r.data)
      setPassword('')
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
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось получить документы Saby')
    } finally {
      setLoading(false)
    }
  }

  const shown = onlyUnsigned ? docs.filter(isUnsigned) : docs

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <h1 style={{ marginTop: 0 }}>Контроль марок</h1>
      <p style={{ color: '#666', marginTop: -8 }}>
        Отлов исходящих УПД, которые покупатель не принял/не подписал — марки «зависают» на
        продавце в ЧЗ. Данные из ЭДО Saby (СБИС).
      </p>

      {/* Подключение Saby */}
      <section style={card}>
        <h2 style={h2}>Подключение Saby (ЭДО)</h2>
        {status?.connected ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span style={badgeOk}>● Подключено</span>
            <span style={{ color: '#444' }}>
              Логин: <b>{status.login}</b>
              {status.account ? ` · Аккаунт: ${status.account}` : ''}
            </span>
            <button style={btnGhost} onClick={() => setStatus({ connected: false })}>
              Изменить доступ
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

          {docs.length > 0 && (
            <div style={{ marginBottom: 10, color: '#444' }}>
              Всего: <b>{docs.length}</b> · Не принято покупателем:{' '}
              <b style={{ color: unsigned ? '#c0392b' : '#2e7d32' }}>{unsigned}</b>
            </div>
          )}

          {shown.length === 0 ? (
            <div style={{ color: '#888', padding: '16px 0' }}>
              {loading ? '' : 'Нет данных — задайте период и нажмите «Показать».'}
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
                        <td style={td}>{d.state_name || (d.state_code != null ? `код ${d.state_code}` : '—')}</td>
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
const table: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 13 }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', borderBottom: '2px solid #eee', color: '#666', fontWeight: 600, whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid #f0f0f0' }
const errBox: React.CSSProperties = { marginTop: 14, padding: '10px 14px', background: '#fdecea', color: '#b71c1c', borderRadius: 6 }
