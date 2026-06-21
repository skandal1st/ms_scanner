import { useEffect, useMemo, useState } from 'react'
import { acceptanceApi, scansApi, productsApi } from '../api/client'
import type {
  ImportUpdResult,
  ImportPositionResult,
  Scan,
  ProductSearchItem,
} from '../api/client'
import { ResizableTable } from '../components/ResizableTable'
import type { ColumnDef } from '../components/ResizableTable'
import { UpdImportBar } from '../components/UpdImportBar'

function errorDetail(e: unknown): string | null {
  const ax = e as { response?: { data?: { detail?: string } } }
  return ax?.response?.data?.detail ?? null
}

export function AcceptancePage() {
  const [docId, setDocId] = useState<string | null>(null)
  const [result, setResult] = useState<ImportUpdResult | null>(null)
  const [scans, setScans] = useState<Scan[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (file: File, group: string) => {
    setBusy(true)
    setError(null)
    setResult(null)
    setScans([])
    try {
      const { data: doc } = await acceptanceApi.createDoc(
        `Приёмка — ${file.name}`,
        group,
      )
      setDocId(doc.id)
      const { data: imp } = await acceptanceApi.importUpd(doc.id, file)
      setResult(imp)
      const { data: sc } = await scansApi.list(doc.id)
      setScans(sc)
    } catch (e) {
      setError(errorDetail(e) ?? 'Не удалось загрузить файл')
    } finally {
      setBusy(false)
    }
  }

  // Коды маркировки по GTIN — для разворота строки позиции.
  const codesByGtin = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const s of scans) {
      const key = s.gtin ?? '∅'
      const arr = m.get(key) ?? []
      arr.push(s.code)
      m.set(key, arr)
    }
    return m
  }, [scans])

  const refreshAfterLink = async () => {
    if (!docId) return
    const { data: sc } = await scansApi.list(docId)
    setScans(sc)
    setResult((r) =>
      r
        ? {
            ...r,
            positions: r.positions.map((p) => {
              const linked = sc.find(
                (s) => s.gtin === p.gtin && s.moysklad_product_id,
              )
              return linked
                ? {
                    ...p,
                    matched: true,
                    product_id: linked.moysklad_product_id ?? p.product_id,
                    product_name: linked.product_name ?? p.product_name,
                  }
                : p
            }),
          }
        : r,
    )
  }

  const columns: ColumnDef<ImportPositionResult>[] = useMemo(
    () => [
      {
        key: 'name',
        header: 'Позиция (УПД)',
        width: 260,
        minWidth: 120,
        render: (p) => p.name,
      },
      {
        key: 'product',
        header: 'Товар (МойСклад)',
        width: 240,
        minWidth: 120,
        render: (p) =>
          p.product_name ?? (
            <span className="text-muted">— не сопоставлен —</span>
          ),
      },
      {
        key: 'gtin',
        header: 'GTIN',
        width: 150,
        minWidth: 90,
        className: 'is-code',
        render: (p) => p.gtin ?? '—',
      },
      {
        key: 'qty',
        header: 'Кол-во',
        width: 80,
        minWidth: 60,
        render: (p) => (p.quantity ?? '—'),
      },
      {
        key: 'codes',
        header: 'Марок',
        width: 90,
        minWidth: 60,
        render: (p) =>
          p.codes_count + (p.packages_count ? ` (+${p.packages_count} уп.)` : ''),
      },
      {
        key: 'status',
        header: 'Статус',
        width: 130,
        minWidth: 90,
        render: (p) =>
          p.matched ? (
            <span className="badge badge--ok">сопоставлен</span>
          ) : (
            <span className="badge badge--warn">нужен товар</span>
          ),
      },
    ],
    [],
  )

  const positions = result?.positions ?? []

  return (
    <div className="acc-page">
      <header className="acc-header">
        <h1 className="acc-header__title">Приёмка маркировки (УПД)</h1>
        <span className="acc-header__doc">
          {result ? `Позиций: ${positions.length}` : 'Файл не загружен'}
        </span>
      </header>

      <div style={{ padding: '12px 16px 0' }}>
        <UpdImportBar busy={busy} onSubmit={handleSubmit} />

        {error && (
          <div className="alert alert--error" style={{ marginTop: 10 }}>
            {error}
          </div>
        )}

        {result && (
          <div className="upd-summary">
            Загружено марок: <b>{result.created_scans}</b>
            {result.skipped_duplicates > 0 && (
              <> · пропущено дублей: {result.skipped_duplicates}</>
            )}
            {result.unmatched_gtins.length > 0 && (
              <>
                {' '}
                · <span style={{ color: '#9a3412' }}>
                  не сопоставлено GTIN: {result.unmatched_gtins.length}
                </span>
              </>
            )}
          </div>
        )}
      </div>

      <div className="acc-body" style={{ display: 'block', padding: '12px 16px' }}>
        {result ? (
          <div className="acc-table-wrap" style={{ background: '#fff' }}>
            <ResizableTable
              columns={columns}
              rows={positions}
              rowKey={(p) => `${p.gtin ?? 'none'}|${p.name}`}
              storageKey="acceptance_table_widths"
              emptyText="В файле нет товарных позиций"
              rowClassName={(p) => (p.matched ? undefined : 'is-unmatched')}
              renderExpanded={(p) => (
                <ExpandedPosition
                  position={p}
                  codes={p.gtin ? codesByGtin.get(p.gtin) ?? [] : []}
                  documentId={docId}
                  onLinked={refreshAfterLink}
                />
              )}
            />
          </div>
        ) : (
          <p className="text-muted" style={{ fontSize: 13 }}>
            Выберите товарную группу и загрузите файл УПД (XML формата ФНС 5.03) —
            система распознает позиции и коды маркировки и сопоставит их с товарами
            МойСклад по GTIN.
          </p>
        )}
      </div>
    </div>
  )
}

function ExpandedPosition({
  position,
  codes,
  documentId,
  onLinked,
}: {
  position: ImportPositionResult
  codes: string[]
  documentId: string | null
  onLinked: () => void
}) {
  const shown = codes.slice(0, 50)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {!position.matched && position.gtin && documentId && (
        <InlineProductPicker
          documentId={documentId}
          gtin={position.gtin}
          onLinked={onLinked}
        />
      )}
      <div>
        <div className="field-label" style={{ marginBottom: 4 }}>
          Коды маркировки {codes.length > 0 ? `(${codes.length})` : ''}
        </div>
        {codes.length === 0 ? (
          <span className="text-muted" style={{ fontSize: 12 }}>
            Нет кодов для этой позиции
          </span>
        ) : (
          <ul className="upd-codes">
            {shown.map((c) => (
              <li key={c} className="code">
                {c}
              </li>
            ))}
            {codes.length > shown.length && (
              <li className="text-muted" style={{ fontSize: 11, listStyle: 'none' }}>
                …и ещё {codes.length - shown.length}
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  )
}

function InlineProductPicker({
  documentId,
  gtin,
  onLinked,
}: {
  documentId: string
  gtin: string
  onLinked: () => void
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ProductSearchItem[]>([])
  const [searching, setSearching] = useState(false)
  const [linking, setLinking] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
        setError(errorDetail(e) ?? 'Ошибка поиска товаров')
        setResults([])
      } finally {
        setSearching(false)
      }
    }, 300)
    return () => window.clearTimeout(handle)
  }, [query])

  const link = async (p: ProductSearchItem) => {
    setLinking(true)
    setError(null)
    try {
      await productsApi.linkGtin(documentId, gtin, p.id, p.name)
      setQuery('')
      setResults([])
      onLinked()
    } catch (e) {
      setError(errorDetail(e) ?? 'Не удалось привязать товар')
    } finally {
      setLinking(false)
    }
  }

  return (
    <div className="upd-picker">
      <div className="upd-picker__head">
        Сопоставьте GTIN <code>{gtin}</code> с товаром МойСклад:
      </div>
      <input
        type="text"
        className="ui-input ui-input--block"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Название товара или артикул…"
        spellCheck={false}
        autoComplete="off"
        disabled={linking}
      />
      {searching && (
        <span className="text-muted" style={{ fontSize: 11 }}>
          Ищу…
        </span>
      )}
      {error && (
        <div className="alert alert--error" style={{ margin: 0 }}>
          {error}
        </div>
      )}
      {results.length > 0 && (
        <ul className="upd-picker__results">
          {results.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className="upd-picker__result"
                onClick={() => void link(p)}
                disabled={linking}
              >
                <span style={{ fontWeight: 500 }}>{p.name}</span>
                {(p.article || p.barcodes.length > 0) && (
                  <span className="text-muted" style={{ fontSize: 11 }}>
                    {p.article && <>арт. {p.article} </>}
                    {p.barcodes.length > 0 && <>· {p.barcodes.slice(0, 3).join(', ')}</>}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {!searching && query.trim().length >= 2 && results.length === 0 && !error && (
        <div className="text-muted" style={{ fontSize: 12, fontStyle: 'italic' }}>
          Ничего не найдено. Добавьте товар в МойСклад и повторите.
        </div>
      )}
    </div>
  )
}
