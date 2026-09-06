import { useEffect, useState } from 'react'
import {
  inventoryApi,
  productsApi,
  type InventoryUnmatchedItem,
  type ProductSearchItem,
} from '../api/client'

const nf = (n: number) => n.toLocaleString('ru')
const PAGE = 200

interface RowState {
  name: string
  query: string
  options: ProductSearchItem[]
  selectedId: string
  searching: boolean
  busy: boolean
  done?: 'named' | 'linked'
  error?: string
}

function productLabel(p: ProductSearchItem): string {
  const extra = [p.article, p.barcodes?.[0]].filter(Boolean).join(' · ')
  return extra ? `${p.name} — ${extra}` : p.name
}

// Внешний поиск по GTIN (реестр GS1 RUS). Для ручного определения товара по коду.
const gs1Url = (gtin: string) => `https://search.gs1ru.org/gtin/${encodeURIComponent(gtin.trim())}`

/**
 * Ручная обработка «не опознанных» позиций (нет ни в МС, ни в базе имён). По каждому GTIN
 * можно: (1) прописать наименование вручную (→ gtin_name_map, позиция получает имя), либо
 * (2) найти и привязать товар МС. Ссылка на GS1 — чтобы определить товар по коду.
 */
export function InventoryResolvePanel({
  brand,
  onClose,
  onResolved,
}: {
  brand: string
  onClose: () => void
  onResolved: () => void
}) {
  const [items, setItems] = useState<InventoryUnmatchedItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [rs, setRs] = useState<Record<string, RowState>>({})
  const [resolvedCount, setResolvedCount] = useState(0)

  const load = async (offset: number) => {
    setLoading(true)
    try {
      const r = await inventoryApi.unmatched(brand, PAGE, offset)
      setTotal(r.data.total)
      setItems((prev) => (offset === 0 ? r.data.items : [...prev, ...r.data.items]))
      setRs((prev) => {
        const next = { ...prev }
        for (const it of r.data.items) {
          const k = it.gtin || ''
          if (!next[k]) next[k] = { name: '', query: '', options: [], selectedId: '', searching: false, busy: false }
        }
        return next
      })
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось загрузить список')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setItems([])
    setRs({})
    load(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brand])

  const patch = (k: string, p: Partial<RowState>) =>
    setRs((s) => ({ ...s, [k]: { ...s[k], ...p } }))

  const saveName = async (it: InventoryUnmatchedItem) => {
    const k = it.gtin || ''
    const st = rs[k]
    if (!st?.name.trim()) return
    patch(k, { busy: true, error: undefined })
    try {
      await inventoryApi.setName(it.gtin || '', st.name.trim())
      patch(k, { busy: false, done: 'named' })
      setResolvedCount((c) => c + 1)
      onResolved()
    } catch (e: any) {
      patch(k, { busy: false, error: e?.response?.data?.detail || 'Не удалось сохранить' })
    }
  }

  const runSearch = async (k: string) => {
    const st = rs[k]
    if (!st?.query.trim()) return
    patch(k, { searching: true })
    try {
      const r = await productsApi.search(st.query.trim())
      patch(k, { options: r.data, selectedId: r.data[0]?.id ?? '', searching: false })
    } catch (e: any) {
      patch(k, { searching: false, error: e?.response?.data?.detail || 'Поиск не удался' })
    }
  }

  const linkMs = async (it: InventoryUnmatchedItem) => {
    const k = it.gtin || ''
    const st = rs[k]
    if (!st?.selectedId) return
    const opt = st.options.find((o) => o.id === st.selectedId)
    patch(k, { busy: true, error: undefined })
    try {
      await inventoryApi.linkGtin(it.gtin || '', st.selectedId, opt?.name ?? (st.name.trim() || null))
      patch(k, { busy: false, done: 'linked' })
      setResolvedCount((c) => c + 1)
      onResolved()
    } catch (e: any) {
      patch(k, { busy: false, error: e?.response?.data?.detail || 'Не удалось привязать' })
    }
  }

  return (
    <div className="card" style={{ padding: 18, marginTop: 12 }}>
      <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 className="mc-tool__title" style={{ margin: 0 }}>Не опознанные позиции</h3>
        <button className="button button--sm" onClick={onClose}>Закрыть</button>
      </div>
      <p className="mc-tool__desc" style={{ marginTop: 8 }}>
        Позиции без имени и без товара в МС. Определите товар по GTIN (ссылка «GS1») и либо
        пропишите наименование вручную, либо найдите и привяжите товар МС. Крупные (по маркам ЧЗ)
        идут первыми. После обработки — «Пересверить» (а после привязок к МС — обновить остаток МС).
      </p>

      {err && <div className="alert alert--error" style={{ marginTop: 8 }}>{err}</div>}
      {resolvedCount > 0 && (
        <div className="alert alert--ok" style={{ marginTop: 8 }}>Обработано: <b>{nf(resolvedCount)}</b>.</div>
      )}

      {loading && items.length === 0 ? (
        <div className="mc-empty">Загрузка…</div>
      ) : items.length === 0 ? (
        <div className="mc-empty">Не опознанных позиций нет.</div>
      ) : (
        <>
          <div className="mc-run-result" style={{ margin: '8px 0' }}>
            Всего: <b>{nf(total)}</b> · показано <b>{nf(items.length)}</b>
          </div>
          <div className="mc-table-wrap">
            <table className="ui-table" style={{ tableLayout: 'fixed', width: '100%' }}>
              <colgroup>
                <col style={{ width: 190 }} />
                <col style={{ width: 64 }} />
                <col />
                <col />
              </colgroup>
              <thead>
                <tr>
                  <th>GTIN</th>
                  <th className="mc-num">ЧЗ</th>
                  <th>Прописать имя</th>
                  <th>…или привязать товар МС</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, i) => {
                  const k = it.gtin || ''
                  const st = rs[k]
                  if (!st) return null
                  return (
                    <tr key={k + i} style={st.done ? { opacity: 0.55 } : undefined}>
                      <td className="tabular" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {it.gtin || '—'}
                        {it.gtin && (
                          <>
                            {' '}
                            <a href={gs1Url(it.gtin)} target="_blank" rel="noopener noreferrer"
                              style={{ fontSize: 12 }}>GS1 ↗</a>
                          </>
                        )}
                      </td>
                      <td className="mc-num">{nf(it.qty_cz)}</td>
                      {st.done ? (
                        <td colSpan={2} className="text-muted">
                          {st.done === 'named' ? 'Имя сохранено ✓' : 'Привязано к МС ✓'}
                        </td>
                      ) : (
                        <>
                          <td>
                            <div className="flex-row gap-8">
                              <input
                                className="ui-input"
                                style={{ flex: 1, minWidth: 0 }}
                                placeholder="Наименование товара"
                                value={st.name}
                                onChange={(e) => patch(k, { name: e.target.value })}
                                onKeyDown={(e) => { if (e.key === 'Enter') saveName(it) }}
                              />
                              <button className="button button--primary button--sm"
                                disabled={!st.name.trim() || st.busy}
                                onClick={() => saveName(it)}>
                                {st.busy ? '…' : 'Сохранить'}
                              </button>
                            </div>
                            {st.error && (
                              <div style={{ color: 'var(--st-err-fg)', fontSize: 12, marginTop: 4 }}>{st.error}</div>
                            )}
                          </td>
                          <td>
                            <div className="flex-row gap-8">
                              <input
                                className="ui-input"
                                style={{ flex: 1, minWidth: 0 }}
                                placeholder="Поиск в МС (имя/артикул)…"
                                value={st.query}
                                onChange={(e) => patch(k, { query: e.target.value })}
                                onKeyDown={(e) => { if (e.key === 'Enter') runSearch(k) }}
                              />
                              <button className="button button--sm" disabled={st.searching}
                                onClick={() => runSearch(k)}>
                                {st.searching ? '…' : 'Найти'}
                              </button>
                            </div>
                            {st.options.length > 0 && (
                              <div className="flex-row gap-8" style={{ marginTop: 6 }}>
                                <select className="ui-input" style={{ flex: 1, minWidth: 0 }} value={st.selectedId}
                                  onChange={(e) => patch(k, { selectedId: e.target.value })}>
                                  {st.options.map((o) => (
                                    <option key={o.id} value={o.id}>{productLabel(o)}</option>
                                  ))}
                                </select>
                                <button className="button button--primary button--sm"
                                  disabled={!st.selectedId || st.busy}
                                  onClick={() => linkMs(it)}>
                                  {st.busy ? '…' : 'Привязать'}
                                </button>
                              </div>
                            )}
                          </td>
                        </>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {items.length < total && (
            <div style={{ marginTop: 10 }}>
              <button className="button" disabled={loading} onClick={() => load(items.length)}>
                {loading ? 'Загрузка…' : `Показать ещё (${nf(total - items.length)})`}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
