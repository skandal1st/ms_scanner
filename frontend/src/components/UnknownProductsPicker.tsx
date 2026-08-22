import { useEffect, useMemo, useState } from 'react'
import { productsApi } from '../api/client'
import type { ProductSearchItem, Scan, MatchSuggestion } from '../api/client'
import { useScanStore, normalizeGtinKey } from '../store/scanStore'
import { useMatchSuggestions } from '../hooks/useDocuments'
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
  const updateScan = useScanStore((s) => s.updateScan)
  const groups = useMemo(() => groupUnknown(scans), [scans])

  const sugQuery = useMatchSuggestions(documentId ?? null, groups.length > 0)
  const sugMap = useMemo(() => {
    const m = new Map<string, MatchSuggestion>()
    for (const s of sugQuery.data ?? []) m.set(s.gtin_key, s)
    return m
  }, [sugQuery.data])

  const [confirmingAll, setConfirmingAll] = useState(false)
  const highGroups = useMemo(
    () => groups.filter((g) => sugMap.get(g.gtinKey)?.confidence === 'high'),
    [groups, sugMap],
  )

  if (!documentId || groups.length === 0) return null

  const confirmAll = async () => {
    const links = highGroups
      .map((g) => ({ g, sug: sugMap.get(g.gtinKey) }))
      .filter((x) => x.sug?.best)
      .map((x) => ({
        gtin: x.g.displayGtin,
        moysklad_product_id: x.sug!.best!.id,
        product_name: x.sug!.best!.name,
      }))
    if (links.length === 0) return
    setConfirmingAll(true)
    try {
      await productsApi.linkGtinBulk(documentId, links)
      for (const g of highGroups) {
        const best = sugMap.get(g.gtinKey)?.best
        if (!best) continue
        for (const s of g.scans) {
          updateScan(s.id, {
            moysklad_product_id: best.id,
            product_name: best.name,
            status: 'valid',
            error_message: null,
          })
        }
      }
      void sugQuery.refetch()
    } finally {
      setConfirmingAll(false)
    }
  }

  return (
    <div style={styles.wrap}>
      <div style={styles.head}>
        <span style={styles.title}>
          ⚠ Нужно сопоставить с товаром в МС: {groups.length}
        </span>
        <span style={styles.subtitle}>
          Эти коды не уйдут в отгрузку без привязки к товару
        </span>
        {sugQuery.isLoading && (
          <span style={styles.subtitle}>Подбираю товары в МойСклад…</span>
        )}
        {highGroups.length > 0 && (
          <button
            type="button"
            className="button button--success"
            style={{ marginTop: 6, alignSelf: 'flex-start' }}
            onClick={confirmAll}
            disabled={confirmingAll}
          >
            {confirmingAll
              ? 'Привязываю…'
              : `✓ Подтвердить все точные (${highGroups.length})`}
          </button>
        )}
      </div>
      {groups.map((g) => (
        <UnknownGroupRow
          key={g.gtinKey}
          group={g}
          documentId={documentId}
          suggestion={sugMap.get(g.gtinKey)}
        />
      ))}
    </div>
  )
}

function UnknownGroupRow({
  group,
  documentId,
  suggestion,
}: {
  group: UnknownGroup
  documentId: string
  suggestion?: MatchSuggestion
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
      {suggestion?.name && (
        <div style={styles.suggestedName}>Из УПД/ЧЗ: {suggestion.name}</div>
      )}
      {suggestion?.best && suggestion.confidence !== 'none' && (
        <div
          style={{
            ...styles.suggestBox,
            ...(suggestion.confidence === 'high'
              ? styles.suggestHigh
              : styles.suggestLow),
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={styles.suggestBadge}>
              {suggestion.confidence === 'high' ? '✓ Точное совпадение' : '≈ Проверьте'}
            </div>
            <div style={styles.resultName}>{suggestion.best.name}</div>
            {suggestion.best.article && (
              <div style={styles.resultMeta}>арт. {suggestion.best.article}</div>
            )}
          </div>
          <button
            type="button"
            className="button button--success"
            onClick={() => void link(suggestion.best as ProductSearchItem)}
            disabled={linking}
          >
            Подтвердить
          </button>
        </div>
      )}
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
    padding: 14,
    background: 'var(--st-warn-bg)',
    border: '1px solid var(--st-warn-bd)',
    borderRadius: 'var(--r-md)',
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
    color: 'var(--st-warn-fg)',
  },
  subtitle: {
    fontSize: 11,
    color: 'var(--st-warn-fg)',
    opacity: 0.85,
  },
  row: {
    background: 'var(--ms-bg)',
    border: '1px solid var(--st-warn-bd)',
    borderRadius: 'var(--r-sm)',
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
    fontFamily: 'var(--ms-font-mono)',
    color: 'var(--ms-text)',
  },
  count: {
    fontSize: 11,
    color: 'var(--ms-text-muted)',
    fontVariantNumeric: 'tabular-nums',
  },
  suggestedName: {
    fontSize: 11,
    color: 'var(--ms-text-muted)',
  },
  suggestBox: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: 8,
    borderRadius: 'var(--r-sm)',
    border: '1px solid',
  },
  suggestHigh: {
    background: 'var(--st-ok-bg)',
    borderColor: 'var(--st-ok-bd)',
  },
  suggestLow: {
    background: 'var(--st-warn-bg)',
    borderColor: 'var(--st-warn-bd)',
  },
  suggestBadge: {
    fontSize: 10,
    fontWeight: 600,
    color: 'var(--ms-text-muted)',
    marginBottom: 2,
  },
  searchRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  input: {
    flex: 1,
    padding: '8px 10px',
    border: '1px solid var(--ms-input-border)',
    borderRadius: 'var(--r-sm)',
    fontSize: 13,
    minWidth: 0,
  },
  muted: {
    fontSize: 11,
    color: 'var(--ms-text-muted)',
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
    padding: '7px 9px',
    border: '1px solid var(--ms-border-light)',
    background: 'var(--ms-bg)',
    borderRadius: 'var(--r-sm)',
    cursor: 'pointer',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    fontSize: 12,
  },
  resultName: {
    color: 'var(--ms-text)',
    fontWeight: 500,
  },
  resultMeta: {
    color: 'var(--ms-text-muted)',
    fontSize: 11,
    display: 'flex',
    gap: 10,
    flexWrap: 'wrap',
  },
  empty: {
    fontSize: 12,
    color: 'var(--ms-text-muted)',
    fontStyle: 'normal',
  },
}
