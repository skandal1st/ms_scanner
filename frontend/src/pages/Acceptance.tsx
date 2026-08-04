import { useEffect, useMemo, useState } from 'react'
import { acceptanceApi, scansApi, productsApi, documentsApi } from '../api/client'
import type {
  AcceptanceDoc,
  ImportUpdResult,
  ImportPositionResult,
  Scan,
  ProductSearchItem,
  MatchSuggestion,
} from '../api/client'
import { ResizableTable } from '../components/ResizableTable'
import type { ColumnDef } from '../components/ResizableTable'
import { UpdImportBar } from '../components/UpdImportBar'
import { useMatchSuggestions } from '../hooks/useDocuments'
import { normalizeGtinKey } from '../store/scanStore'

function errorDetail(e: unknown): string | null {
  const ax = e as { response?: { data?: { detail?: string } } }
  return ax?.response?.data?.detail ?? null
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

const rub = (v: number) =>
  v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function AcceptancePage() {
  const [doc, setDoc] = useState<AcceptanceDoc | null>(null)
  const [result, setResult] = useState<ImportUpdResult | null>(null)
  const [scans, setScans] = useState<Scan[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [sendDone, setSendDone] = useState(false)

  const docId = doc?.id ?? null

  const handleSubmit = async (file: File, group: string, moyskladId: string) => {
    setBusy(true)
    setError(null)
    setResult(null)
    setScans([])
    setDoc(null)
    setSendError(null)
    setSendDone(false)
    try {
      const { data: created } = await acceptanceApi.createDoc(
        `Приёмка — ${file.name}`,
        group,
        moyskladId || undefined,
      )
      setDoc(created)
      const { data: imp } = await acceptanceApi.importUpd(created.id, file)
      setResult(imp)
      const { data: sc } = await scansApi.list(created.id)
      setScans(sc)
    } catch (e) {
      setError(errorDetail(e) ?? 'Не удалось загрузить файл')
    } finally {
      setBusy(false)
    }
  }

  // Ручной список марок: дополняет текущий документ, если он уже открыт (напр.
  // после УПД); иначе создаёт новый по выбранной группе/поступлению.
  const handleSubmitMarks = async (
    codes: string[],
    group: string,
    moyskladId: string,
  ) => {
    setBusy(true)
    setError(null)
    setSendError(null)
    setSendDone(false)
    try {
      let target = doc
      if (!target) {
        setResult(null)
        setScans([])
        const { data: created } = await acceptanceApi.createDoc(
          'Приёмка — список марок',
          group,
          moyskladId || undefined,
        )
        target = created
        setDoc(created)
      }
      const { data: imp } = await acceptanceApi.importMarks(target.id, codes)
      setResult(imp)
      const { data: sc } = await scansApi.list(target.id)
      setScans(sc)
    } catch (e) {
      setError(errorDetail(e) ?? 'Не удалось загрузить список марок')
      throw e
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

  // Несопоставленные позиции — наверх списка (их нужно разобрать в первую очередь),
  // внутри групп сохраняем исходный порядок УПД (стабильная сортировка).
  const positions = useMemo(() => {
    const src = result?.positions ?? []
    return [...src].sort((a, b) => Number(a.matched) - Number(b.matched))
  }, [result])
  const unmatched = positions.filter((p) => !p.matched && p.gtin)

  // Авто-подсказки сопоставления GTIN↔товар (МС-поиск по имени из УПД/ЧЗ).
  const sugQuery = useMatchSuggestions(docId, unmatched.length > 0)
  const sugMap = useMemo(() => {
    const m = new Map<string, MatchSuggestion>()
    for (const s of sugQuery.data ?? []) m.set(s.gtin_key, s)
    return m
  }, [sugQuery.data])
  const sugFor = (g?: string | null): MatchSuggestion | undefined =>
    g ? sugMap.get(normalizeGtinKey(g) ?? '') : undefined
  const [confirmingAll, setConfirmingAll] = useState(false)
  const highUnmatched = useMemo(
    () => unmatched.filter((p) => sugFor(p.gtin)?.confidence === 'high'),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [unmatched, sugMap],
  )

  const confirmAllSuggestions = async () => {
    if (!docId) return
    const links = highUnmatched
      .map((p) => ({ gtin: p.gtin as string, sug: sugFor(p.gtin) }))
      .filter((x) => x.sug?.best)
      .map((x) => ({
        gtin: x.gtin,
        moysklad_product_id: x.sug!.best!.id,
        product_name: x.sug!.best!.name,
      }))
    if (links.length === 0) return
    setConfirmingAll(true)
    try {
      await productsApi.linkGtinBulk(docId, links)
      await refreshAfterLink()
      void sugQuery.refetch()
    } finally {
      setConfirmingAll(false)
    }
  }
  const linkedToMs = !!doc?.moysklad_id
  const alreadyAccepted = doc?.status === 'accepted'

  const handleSend = async () => {
    if (!docId) return
    setSending(true)
    setSendError(null)
    try {
      await documentsApi.process(docId)
      // Запись в МС идёт в Celery; опрашиваем статус документа до accepted.
      let finalStatus = 'processing'
      let failReason: string | null = null
      for (let i = 0; i < 20; i++) {
        await sleep(1500)
        const { data: fresh } = await acceptanceApi.getDoc(docId)
        finalStatus = fresh.status
        setDoc(fresh)
        if (fresh.status === 'accepted') break
        // Воркер выставил причину неуспеха (напр. истёк токен ЧЗ) — прекращаем
        // опрос и показываем её, а не ждём ложное «ещё обрабатывается».
        if (fresh.error_message) {
          failReason = fresh.error_message
          break
        }
      }
      if (failReason) {
        setSendError(failReason)
      } else if (finalStatus === 'accepted') {
        setSendDone(true)
        if (window.opener && !window.opener.closed) {
          setTimeout(() => window.close(), 1500)
        }
      } else {
        setSendError(
          'МойСклад ещё обрабатывает документ. Обновите страницу позже, чтобы увидеть результат.',
        )
      }
    } catch (e) {
      setSendError(errorDetail(e) ?? 'Не удалось отправить в МойСклад')
    } finally {
      setSending(false)
    }
  }

  const columns: ColumnDef<ImportPositionResult>[] = useMemo(
    () => [
      {
        key: 'line',
        header: '№',
        width: 56,
        minWidth: 40,
        render: (p) => p.line_number ?? '—',
      },
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

  return (
    <div className="acc-page">
      <header className="acc-header">
        <h1 className="acc-header__title">Приёмка маркировки (УПД)</h1>
        <span className="acc-header__doc">
          {result ? `Позиций: ${positions.length}` : 'Файл не загружен'}
        </span>
      </header>

      <div className="acc-scroll">
      <div style={{ padding: '12px 16px 0' }}>
        <UpdImportBar busy={busy} onSubmit={handleSubmit} onSubmitMarks={handleSubmitMarks} />

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
            {result.total_amount != null && (
              <> · сумма по УПД: <b>{rub(result.total_amount)} ₽</b></>
            )}
            {result.total_vat != null && (
              <> · в т.ч. НДС: <b>{rub(result.total_vat)} ₽</b></>
            )}
            {linkedToMs ? (
              <> · привязано к поступлению МС</>
            ) : (
              <> · <span style={{ color: '#9a3412' }}>без записи в МС</span></>
            )}
          </div>
        )}
      </div>

      {/* Видимая панель сопоставления: подбор товара для несопоставленных GTIN
          без необходимости разворачивать строку таблицы. */}
      {result && unmatched.length > 0 && (
        <div style={{ padding: '12px 16px 0' }}>
          <div
            style={{
              background: '#fffbeb',
              border: '1px solid #fde68a',
              borderRadius: 8,
              padding: 12,
            }}
          >
            <div
              className="field-label"
              style={{ margin: 0, marginBottom: 8, color: '#92400e' }}
            >
              Сопоставьте товары МойСклад ({unmatched.length}) — без этого приёмку
              нельзя отправить в МС
            </div>
            {sugQuery.isLoading && (
              <div className="text-muted" style={{ fontSize: 11, marginBottom: 8 }}>
                Подбираю товары в МойСклад…
              </div>
            )}
            {highUnmatched.length > 0 && (
              <button
                type="button"
                className="button button--success"
                style={{ marginBottom: 10 }}
                onClick={confirmAllSuggestions}
                disabled={confirmingAll}
              >
                {confirmingAll
                  ? 'Привязываю…'
                  : `✓ Подтвердить все точные (${highUnmatched.length})`}
              </button>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {unmatched.map((p) => (
                <div key={`${p.gtin}|${p.name}`}>
                  <div style={{ fontSize: 13, marginBottom: 4 }}>
                    <b>{p.name}</b>{' '}
                    <span className="text-muted" style={{ fontSize: 11 }}>
                      GTIN {p.gtin}
                    </span>
                  </div>
                  {docId && p.gtin && (
                    <InlineProductPicker
                      documentId={docId}
                      gtin={p.gtin}
                      onLinked={refreshAfterLink}
                      suggestion={sugFor(p.gtin)}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <div style={{ padding: '12px 16px' }}>
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
                  codes={p.gtin ? codesByGtin.get(p.gtin) ?? [] : []}
                  documentId={docId}
                  gtin={p.gtin}
                  currentProductName={p.product_name}
                  canRelink={linkedToMs && !alreadyAccepted}
                  onLinked={refreshAfterLink}
                />
              )}
            />
          </div>
        ) : (
          <p className="text-muted" style={{ fontSize: 13 }}>
            Выберите товарную группу, поступление МойСклад (куда записать коды) и
            загрузите файл УПД (XML формата ФНС 5.03) — система распознает позиции и
            коды маркировки и сопоставит их с товарами поступления по GTIN.
          </p>
        )}
      </div>
      </div>

      {result && (
        <footer className="acc-footer">
          {!linkedToMs && (
            <span className="text-muted" style={{ fontSize: 12 }}>
              Поступление МС не выбрано — отправка в МС недоступна.
            </span>
          )}
          {sendError && (
            <span style={{ fontSize: 12, color: '#b91c1c' }}>{sendError}</span>
          )}
          <div className="acc-footer__spacer" />
          <button
            type="button"
            className="button button--success"
            disabled={
              !linkedToMs ||
              scans.length === 0 ||
              unmatched.length > 0 ||
              sending ||
              alreadyAccepted
            }
            title={
              !linkedToMs
                ? 'Выберите поступление МойСклад при загрузке УПД'
                : unmatched.length > 0
                  ? `Сначала сопоставьте товары (${unmatched.length})`
                  : undefined
            }
            onClick={handleSend}
          >
            {alreadyAccepted
              ? 'Отправлено в МС ✓'
              : sending
                ? 'Отправка в МС…'
                : unmatched.length > 0
                  ? `Сопоставьте товары (${unmatched.length})`
                  : 'Отправить приёмку в МС'}
          </button>
        </footer>
      )}

      {sendDone && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(255,255,255,0.92)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            fontFamily: 'system-ui, -apple-system, sans-serif',
          }}
        >
          <div style={{ textAlign: 'center', color: '#1f2937' }}>
            <div style={{ fontSize: 18, fontWeight: 500, marginBottom: 6 }}>
              Приёмка отправлена в МойСклад ✓
            </div>
            <div style={{ fontSize: 13, color: '#6b7280' }}>
              Коды маркировки записаны в позиции поступления.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ExpandedPosition({
  codes,
  documentId,
  gtin,
  currentProductName,
  canRelink,
  onLinked,
}: {
  codes: string[]
  documentId: string | null
  gtin?: string | null
  currentProductName?: string | null
  canRelink: boolean
  onLinked: () => void
}) {
  const shown = codes.slice(0, 50)
  return (
    <div>
      {canRelink && documentId && gtin && (
        <div style={{ marginBottom: 12 }}>
          <div className="field-label" style={{ marginBottom: 4 }}>
            Изменить товар МойСклад для GTIN {gtin}
            {currentProductName && (
              <span className="text-muted" style={{ fontWeight: 400 }}>
                {' '}(сейчас: {currentProductName})
              </span>
            )}
          </div>
          <InlineProductPicker
            documentId={documentId}
            gtin={gtin}
            onLinked={onLinked}
          />
        </div>
      )}
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
  )
}

function InlineProductPicker({
  documentId,
  gtin,
  onLinked,
  suggestion,
}: {
  documentId: string
  gtin: string
  onLinked: () => void
  suggestion?: MatchSuggestion
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
      {suggestion?.best && suggestion.confidence !== 'none' && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: 8,
            marginBottom: 8,
            borderRadius: 6,
            border: '1px solid',
            background: suggestion.confidence === 'high' ? '#f0fdf4' : '#fffbeb',
            borderColor: suggestion.confidence === 'high' ? '#86efac' : '#fde68a',
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: '#374151' }}>
              {suggestion.confidence === 'high' ? '✓ Точное совпадение' : '≈ Проверьте'}
            </div>
            <div style={{ fontWeight: 500, fontSize: 13 }}>{suggestion.best.name}</div>
            {suggestion.best.article && (
              <div className="text-muted" style={{ fontSize: 11 }}>
                арт. {suggestion.best.article}
              </div>
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
