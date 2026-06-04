import { useEffect, useMemo, useState } from 'react'
import { productsApi } from '../api/client'
import type { ProductSearchItem, Scan } from '../api/client'
import { useScanStore, normalizeGtinKey } from '../store/scanStore'
import type { CSSProperties } from 'react'

interface UnknownGroup {
  gtinKey: string
  displayGtin: string
  count: number
  scans: Scan[]
}

function groupUnknown(scans: Scan[]): UnknownGroup[] {
  const map = new Map<string, UnknownGroup>()
  for (const s of scans) {
    if (s.status !== 'unknown_product') continue
    const key = normalizeGtinKey(s.gtin) ?? s.gtin ?? s.code.slice(0, 32)
    if (!key) continue
    let g = map.get(key)
    if (!g) {
      g = {
        gtinKey: key,
        displayGtin: s.gtin || key,
        count: 0,
        scans: [],
      }
      map.set(key, g)
    }
    g.count += 1
    g.scans.push(s)
  }
  return Array.from(map.values()).sort((a, b) => b.count - a.count)
}

export function UnknownProductsPicker() {
  const documentId = useScanStore((s) => s.document?.id)
  const scans = useScanStore((s) => s.scans)
  const groups = useMemo(() => groupUnknown(scans), [scans])

  if (!documentId || groups.length === 0) return null

  return (
    <div style={styles.wrap}>
      <div style={styles.head}>
        <span style={styles.title}>
          ⚠ Нужно сопоставить с товаром в МС: {groups.length}
        </span>
        <span style={styles.subtitle}>
          Эти коды не уйдут в отгрузку без привязки к товару
        </span>
      </div>
      {groups.map((g) => (
        <UnknownGroupRow key={g.gtinKey} group={g} documentId={documentId} />
      ))}
    </div>
  )
}

function UnknownGroupRow({
  group,
  documentId,
}: {
  group: UnknownGroup
  documentId: string
}) {
  const updateScan = useScanStore((s) => s.updateScan)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ProductSearchItem[]>([])
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setResults([])
      setSearching(false)
      return
    }
    setSearching(true)
    const handle = window.setTimeout(async () => {
      try {
        const { data } = await productsApi.search(q)
        setResults(data)
        setError(null)
      } catch (e) {
        const ax = e as { response?: { data?: { detail?: string } } }
        setError(ax?.response?.data?.detail || 'Ошибка поиска товаров')
        setResults([])
      } finally {
        setSearching(false)
      }
    }, 300)
    return () => window.clearTimeout(handle)
  }, [query])

  const link = async (product: ProductSearchItem) => {
    setLinking(true)
    setError(null)
    try {
      await productsApi.linkGtin(documentId, group.displayGtin, product.id, product.name)
      // Оптимистическое обновление — WS придёт следом и продублирует, это ОК.
      for (const s of group.scans) {
        updateScan(s.id, {
          moysklad_product_id: product.id,
          product_name: product.name,
          status: 'valid',
          error_message: null,
        })
      }
      setQuery('')
      setResults([])
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setError(ax?.response?.data?.detail || 'Не удалось привязать товар')
    } finally {
      setLinking(false)
    }
  }

  return (
    <div style={styles.row}>
      <div style={styles.rowHead}>
        <code style={styles.gtin}>GTIN {group.displayGtin}</code>
        <span style={styles.count}>{group.count} код(ов)</span>
      </div>
      <div style={styles.searchRow}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Название товара или артикул…"
          style={styles.input}
          spellCheck={false}
          autoComplete="off"
          disabled={linking}
        />
        {searching && <span style={styles.muted}>Ищу…</span>}
      </div>
      {error && (
        <div className="alert alert--error" style={{ margin: 0 }}>
          {error}
        </div>
      )}
      {results.length > 0 && (
        <ul style={styles.results}>
          {results.map((p) => (
            <li key={p.id} style={styles.result}>
              <button
                type="button"
                style={styles.resultBtn}
                onClick={() => void link(p)}
                disabled={linking}
              >
                <span style={styles.resultName}>{p.name}</span>
                <span style={styles.resultMeta}>
                  {p.article && <span>арт. {p.article}</span>}
                  {p.barcodes.length > 0 && (
                    <span>штрихкоды: {p.barcodes.slice(0, 3).join(', ')}</span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {!searching && query.trim().length >= 2 && results.length === 0 && !error && (
        <div style={styles.empty}>
          Ничего не найдено. Добавьте товар в номенклатуру МойСклад и повторите поиск.
        </div>
      )}
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  wrap: {
    marginBottom: 12,
    padding: 12,
    background: '#fff7ed',
    border: '1px solid #fdba74',
    borderRadius: 8,
  },
  head: {
    marginBottom: 10,
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  title: {
    fontSize: 13,
    fontWeight: 600,
    color: '#9a3412',
  },
  subtitle: {
    fontSize: 11,
    color: '#9a3412',
    opacity: 0.85,
  },
  row: {
    background: '#fff',
    border: '1px solid #fed7aa',
    borderRadius: 6,
    padding: 10,
    marginBottom: 8,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  rowHead: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    gap: 8,
  },
  gtin: {
    fontSize: 12,
    fontFamily: 'monospace',
    color: '#1f2937',
  },
  count: {
    fontSize: 11,
    color: '#6b7280',
    fontVariantNumeric: 'tabular-nums',
  },
  searchRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  input: {
    flex: 1,
    padding: '8px 10px',
    border: '1px solid #cbd5e1',
    borderRadius: 6,
    fontSize: 13,
    minWidth: 0,
  },
  muted: {
    fontSize: 11,
    color: '#6b7280',
  },
  results: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    maxHeight: 240,
    overflowY: 'auto',
  },
  result: {
    margin: 0,
  },
  resultBtn: {
    width: '100%',
    textAlign: 'left',
    padding: '6px 8px',
    border: '1px solid #e5e7eb',
    background: '#f9fafb',
    borderRadius: 4,
    cursor: 'pointer',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    fontSize: 12,
  },
  resultName: {
    color: '#1f2937',
    fontWeight: 500,
  },
  resultMeta: {
    color: '#6b7280',
    fontSize: 11,
    display: 'flex',
    gap: 10,
    flexWrap: 'wrap',
  },
  empty: {
    fontSize: 12,
    color: '#6b7280',
    fontStyle: 'italic',
  },
}
