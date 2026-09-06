import { useEffect, useState } from 'react'
import {
  inventoryApi,
  productsApi,
  type InventoryMatchItem,
  type ProductSearchItem,
} from '../api/client'

const nf = (n: number) => n.toLocaleString('ru')

interface RowState {
  options: ProductSearchItem[]
  selectedId: string
  query: string
  searching: boolean
  linking: boolean
  done: boolean
  error?: string
}

const CONF: Record<string, { label: string; color: string }> = {
  high: { label: 'точное', color: '#1e7d34' },
  low: { label: 'похоже', color: '#9a6a00' },
  none: { label: 'не найдено', color: 'var(--ms-text-subtle)' },
}

function productLabel(p: ProductSearchItem): string {
  const extra = [p.article, p.barcodes?.[0]].filter(Boolean).join(' · ')
  return extra ? `${p.name} — ${extra}` : p.name
}

/**
 * Подбор товара МС по имени для позиций «есть имя (из УПД), но нет в остатках МС».
 * Показывает лучший кандидат, но позволяет выбрать другой вариант (в т.ч. иную фасовку)
 * — через список подсказок или поиск по каталогу. Привязка пишет GtinProductMap + штрихкод.
 */
export function InventoryMatchPanel({
  brand,
  onClose,
  onLinked,
}: {
  brand: string
  onClose: () => void
  onLinked: () => void
}) {
  const [items, setItems] = useState<InventoryMatchItem[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [rs, setRs] = useState<Record<string, RowState>>({})
  const [linkedCount, setLinkedCount] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true)
    inventoryApi
      .matchSuggestions(brand, 40)
      .then((r) => {
        if (!alive) return
        setItems(r.data.items)
        const init: Record<string, RowState> = {}
        for (const it of r.data.items) {
          const key = it.gtin || ''
          init[key] = {
            options: it.suggestions,
            selectedId: it.best?.id ?? it.suggestions[0]?.id ?? '',
            query: '',
            searching: false,
            linking: false,
            done: false,
          }
        }
        setRs(init)
      })
      .catch((e: any) => setErr(e?.response?.data?.detail || 'Не удалось получить подсказки'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [brand])

  const patch = (key: string, p: Partial<RowState>) =>
    setRs((s) => ({ ...s, [key]: { ...s[key], ...p } }))

  const runSearch = async (key: string) => {
    const st = rs[key]
    if (!st || !st.query.trim()) return
    patch(key, { searching: true })
    try {
      const r = await productsApi.search(st.query.trim())
      // Мержим результаты поиска к текущим опциям (дедуп по id), сохраняя выбор.
      const byId = new Map<string, ProductSearchItem>()
      for (const p of [...r.data, ...st.options]) byId.set(p.id, p)
      const options = [...byId.values()]
      patch(key, { options, selectedId: r.data[0]?.id ?? st.selectedId, searching: false })
    } catch (e: any) {
      patch(key, { searching: false, error: e?.response?.data?.detail || 'Поиск не удался' })
    }
  }

  const link = async (it: InventoryMatchItem) => {
    const key = it.gtin || ''
    const st = rs[key]
    if (!st || !st.selectedId) return
    const opt = st.options.find((o) => o.id === st.selectedId)
    patch(key, { linking: true, error: undefined })
    try {
      await inventoryApi.linkGtin(it.gtin || '', st.selectedId, opt?.name ?? it.name)
      patch(key, { linking: false, done: true })
      setLinkedCount((c) => c + 1)
      onLinked()
    } catch (e: any) {
      patch(key, { linking: false, error: e?.response?.data?.detail || 'Не удалось привязать' })
    }
  }

  const remaining = items.filter((it) => !rs[it.gtin || '']?.done)

  return (
    <div className="card" style={{ padding: 18, marginTop: 12 }}>
      <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 className="mc-tool__title" style={{ margin: 0 }}>
          Подбор товара МС по имени{brand ? ' · выбранный бренд' : ''}
        </h3>
        <button className="button button--sm" onClick={onClose}>Закрыть</button>
      </div>
      <p className="mc-tool__desc" style={{ marginTop: 8 }}>
        Позиции, у которых есть имя из УПД, но нет в остатках МС. Возможно, товар в МС
        переименован или у него не привязан этот GTIN. Выберите правильный товар (можно
        уточнить поиском — особенно если отличается фасовка) и привяжите. Привязка добавит
        GTIN в штрихкоды карточки МС; затем обновите остаток МС и пересверьте.
      </p>

      {err && <div className="alert alert--error" style={{ marginTop: 8 }}>{err}</div>}
      {loading ? (
        <div className="mc-empty">Ищем совпадения в МС…</div>
      ) : items.length === 0 ? (
        <div className="mc-empty">Нет позиций для подбора (все с именем уже есть в МС).</div>
      ) : (
        <>
          {linkedCount > 0 && (
            <div className="alert alert--ok" style={{ marginTop: 8 }}>
              Привязано: <b>{nf(linkedCount)}</b>. Обновите остаток МС и нажмите «Пересверить».
            </div>
          )}
          <div className="mc-table-wrap" style={{ marginTop: 8 }}>
            <table className="ui-table" style={{ tableLayout: 'fixed', width: '100%' }}>
              <colgroup>
                <col />
                <col style={{ width: 64 }} />
                <col style={{ width: 96 }} />
                <col style={{ width: '42%' }} />
                <col style={{ width: 116 }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Товар (из УПД)</th>
                  <th className="mc-num">ЧЗ</th>
                  <th>Совпадение</th>
                  <th>Товар МС</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((it, i) => {
                  const key = it.gtin || ''
                  const st = rs[key]
                  if (!st) return null
                  const conf = CONF[it.confidence] ?? CONF.none
                  return (
                    <tr key={key + i} style={st.done ? { opacity: 0.55 } : undefined}>
                      <td>
                        <div style={{ fontWeight: 500 }}>{it.name || '—'}</div>
                        <div className="text-muted tabular" style={{ fontSize: 12 }}>{it.gtin}</div>
                      </td>
                      <td className="mc-num">{nf(it.qty_cz)}</td>
                      <td style={{ color: conf.color, fontWeight: 600, whiteSpace: 'nowrap' }}>{conf.label}</td>
                      <td style={{ minWidth: 320 }}>
                        {st.done ? (
                          <span className="text-muted">Привязано ✓</span>
                        ) : (
                          <>
                            <select
                              className="ui-input"
                              style={{ width: '100%' }}
                              value={st.selectedId}
                              onChange={(e) => patch(key, { selectedId: e.target.value })}
                            >
                              <option value="">— выберите товар —</option>
                              {st.options.map((o) => (
                                <option key={o.id} value={o.id}>{productLabel(o)}</option>
                              ))}
                            </select>
                            <div className="flex-row gap-8" style={{ marginTop: 6 }}>
                              <input
                                className="ui-input"
                                style={{ flex: 1, minWidth: 0 }}
                                placeholder="Уточнить поиск в МС (имя/артикул)…"
                                value={st.query}
                                onChange={(e) => patch(key, { query: e.target.value })}
                                onKeyDown={(e) => { if (e.key === 'Enter') runSearch(key) }}
                              />
                              <button className="button button--sm" disabled={st.searching}
                                onClick={() => runSearch(key)}>
                                {st.searching ? '…' : 'Найти'}
                              </button>
                            </div>
                            {st.error && <div className="text-muted" style={{ color: 'var(--st-err-fg)', fontSize: 12, marginTop: 4 }}>{st.error}</div>}
                          </>
                        )}
                      </td>
                      <td>
                        {!st.done && (
                          <button
                            className="button button--primary button--sm"
                            disabled={!st.selectedId || st.linking}
                            onClick={() => link(it)}
                          >
                            {st.linking ? 'Привязка…' : 'Привязать'}
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {remaining.length === 0 && (
            <div className="alert alert--ok" style={{ marginTop: 10 }}>
              Все позиции обработаны. Обновите остаток МС и пересверьте.
            </div>
          )}
        </>
      )}
    </div>
  )
}
