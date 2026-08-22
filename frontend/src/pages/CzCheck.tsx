import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  czApi,
  integrationsApi,
  type CzCheckItem,
  type DocumentKind,
} from '../api/client'

const KIND_LABEL: Record<DocumentKind, string> = {
  demand: 'Отгрузка',
  loss: 'Списание',
  supply: 'Приёмка',
}

// Статусы КМ в ГИС МТ (cisInfo.status). Неизвестные показываем как есть.
const STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  INTRODUCED: { label: 'В обороте', cls: 'badge--ok' },
  APPLIED: { label: 'Нанесена', cls: 'badge--warn' },
  EMITTED: { label: 'Эмитирована (не введена)', cls: 'badge--warn' },
  WITHDRAWN: { label: 'Выведена из оборота', cls: 'badge--error' },
  RETIRED: { label: 'Выбыла из оборота', cls: 'badge--error' },
  WRITTEN_OFF: { label: 'Списана', cls: 'badge--error' },
  DISAGGREGATION: { label: 'Расформирована', cls: 'badge--warn' },
}

function statusText(item: CzCheckItem): string {
  if (!item.found) return item.error || 'Не найдена в ЧЗ'
  if (!item.status) return 'В обороте'
  return STATUS_LABEL[item.status]?.label ?? item.status
}

function parseCodes(text: string): string[] {
  return Array.from(
    new Set(
      text
        .split(/[\r\n\t]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  )
}

function downloadCsv(items: CzCheckItem[]) {
  const head = [
    'Код', 'Товар', 'Владелец ИНН', 'Владелец', 'Производитель',
    'Группа', 'Статус', 'Упаковка', 'Документы',
  ]
  const rows = items.map((i) => [
    i.code,
    i.product_name ?? '',
    i.owner_inn ?? '',
    i.owner_name ?? '',
    i.producer_name ?? '',
    i.product_group ?? '',
    statusText(i),
    i.package_type ?? '',
    i.documents.map((d) => `${d.name} (${KIND_LABEL[d.kind] ?? d.kind})`).join('; '),
  ])
  const esc = (v: string) => `"${String(v).replace(/"/g, '""')}"`
  const csv = [head, ...rows].map((r) => r.map(esc).join(',')).join('\r\n')
  // BOM — чтобы Excel корректно открыл кириллицу в UTF-8.
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `проверка-марок-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

export function CzCheckPage() {
  const [text, setText] = useState('')
  const [items, setItems] = useState<CzCheckItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [needCz, setNeedCz] = useState(false)

  const { data: integration } = useQuery({
    queryKey: ['integration'],
    queryFn: () => integrationsApi.get().then((r) => r.data),
  })
  const signatureInn = integration?.cz_inn ?? null

  const codes = useMemo(() => parseCodes(text), [text])

  const handleCheck = async () => {
    if (codes.length === 0) return
    setLoading(true)
    setError(null)
    setNeedCz(false)
    try {
      const { data } = await czApi.check(codes)
      setItems(data.items)
    } catch (e) {
      const ax = e as { response?: { status?: number; data?: { detail?: string } } }
      const detail = ax?.response?.data?.detail
      if (ax?.response?.status === 400 && detail?.includes('Честный Знак')) setNeedCz(true)
      setError(detail || (e instanceof Error ? e.message : String(e)))
    } finally {
      setLoading(false)
    }
  }

  const foundCount = items.filter((i) => i.found).length

  return (
    <div className="acc-page">
      <header className="acc-header">
        <h1 className="acc-header__title">Проверка марок</h1>
        {items.length > 0 && (
          <span className="acc-header__doc">
            Найдено {foundCount} из {items.length}
          </span>
        )}
      </header>

      {needCz && (
        <div role="alert" className="alert alert--error" style={{ margin: '12px 18px 0' }}>
          <span className="alert__spacer">Войдите в Честный Знак — без авторизации проверка невозможна.</span>
          <a href="/settings" className="button button--sm" style={{ whiteSpace: 'nowrap' }}>Войти в ЧЗ</a>
        </div>
      )}

      <div className="section">
        <label className="field-label">
          Отсканируйте марку или вставьте список (по одной в строке)
        </label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="01046403403908892121(qbFHlf..."
          className="ui-input ui-input--block"
          rows={5}
          style={{ fontFamily: 'monospace', fontSize: 12, resize: 'vertical' }}
        />
        <div className="flex-row gap-8" style={{ marginTop: 8, alignItems: 'center' }}>
          <button
            type="button"
            className="button button--success"
            onClick={handleCheck}
            disabled={loading || codes.length === 0}
          >
            {loading ? 'Проверяю…' : `Проверить${codes.length ? ` (${codes.length})` : ''}`}
          </button>
          {items.length > 0 && (
            <button type="button" className="button" onClick={() => downloadCsv(items)}>
              Экспорт (CSV)
            </button>
          )}
          {error && !needCz && (
            <span style={{ color: 'var(--st-err-fg)', fontSize: 12 }}>{error}</span>
          )}
        </div>
      </div>

      {items.length > 0 && (
        <div className="acc-table-wrap" style={{ marginTop: 12 }}>
          <table className="ui-table">
            <thead>
              <tr>
                <th>Код</th>
                <th>Товар</th>
                <th>Владелец</th>
                <th>Производитель</th>
                <th>Группа</th>
                <th>Статус</th>
                <th>Документы</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => {
                const alien =
                  i.found && signatureInn && i.owner_inn && i.owner_inn.trim() !== signatureInn.trim()
                const stCls = !i.found
                  ? 'badge--warn'
                  : i.status
                    ? STATUS_LABEL[i.status]?.cls ?? 'badge--warn'
                    : 'badge--ok'
                const pkg =
                  i.package_type === 'BOX' || i.package_type === 'LEVEL2'
                    ? `Короб${i.child_count ? ` · ${i.child_count}` : ''}`
                    : i.package_type === 'GROUP' || i.package_type === 'LEVEL1'
                      ? `Блок${i.child_count ? ` · ${i.child_count}` : ''}`
                      : ''
                return (
                  <tr key={i.code}>
                    <td style={{ fontFamily: 'monospace', fontSize: 11 }} title={i.code}>
                      {i.code.slice(0, 28)}{i.code.length > 28 ? '…' : ''}
                    </td>
                    <td>
                      {i.product_name || <span className="text-muted">—</span>}
                      {pkg && (
                        <div style={{ fontSize: 10, color: 'var(--brand)', marginTop: 2 }}>{pkg}</div>
                      )}
                    </td>
                    <td>
                      {i.owner_name || i.owner_inn ? (
                        <>
                          {i.owner_name}
                          {i.owner_inn ? (
                            <div className="text-muted" style={{ fontSize: 10 }}>ИНН {i.owner_inn}</div>
                          ) : null}
                          {alien && (
                            <span className="badge badge--warn" style={{ marginTop: 2 }}>
                              Чужой владелец
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td>{i.producer_name || <span className="text-muted">—</span>}</td>
                    <td>{i.product_group || <span className="text-muted">—</span>}</td>
                    <td>
                      <span className={`badge ${stCls}`} title={i.error ?? undefined}>
                        {statusText(i)}
                      </span>
                    </td>
                    <td>
                      {i.documents.length === 0 ? (
                        <span className="text-muted">—</span>
                      ) : (
                        i.documents.map((d) => (
                          <div key={d.document_id} style={{ fontSize: 11 }}>
                            {d.name}{' '}
                            <span className="text-muted">
                              ({KIND_LABEL[d.kind] ?? d.kind} · {d.scan_status})
                            </span>
                          </div>
                        ))
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
