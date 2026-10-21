import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { scansApi, markHistoryApi, type CodeSearchHit, type UpdSearchHit, type MarkHistoryResponse } from '../api/client'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { MarkChronology } from './MarkChronology'
import { captureSerialScans } from '../lib/serialScanCapture'
import { SerialScannerStatus } from './SerialScannerStatus'
import type { UseSerialScannerResult } from '../hooks/useSerialScanner'

type Props = {
  open: boolean
  onClose: () => void
  serial: UseSerialScannerResult
}

const KIND_LABELS: Record<string, string> = {
  supply: 'Приёмка',
  demand: 'Отгрузка',
  loss: 'Списание',
  salesreturn: 'Возврат',
  move: 'Перемещение',
}

const STATUS_LABELS: Record<string, { label: string; cls: string }> = {
  scanned: { label: 'Не проверено', cls: 'badge--pending' },
  pending: { label: 'Проверяется', cls: 'badge--pending' },
  valid: { label: 'Валиден', cls: 'badge--ok' },
  invalid: { label: 'Ошибка', cls: 'badge--error' },
  duplicate: { label: 'Дубль', cls: 'badge--warn' },
  overflow: { label: 'Сверх плана', cls: 'badge--error' },
  unknown_product: { label: 'Нет товара', cls: 'badge--warn' },
  used_in_other_doc: { label: 'В другом документе', cls: 'badge--warn' },
}

function isIncoming(direction: string | null): boolean {
  return (direction ?? '').toLowerCase().startsWith('вход')
}

function isOutgoing(direction: string | null): boolean {
  return (direction ?? '').toLowerCase().startsWith('исход')
}

export function CodeSearchModal({ open, onClose, serial }: Props) {
  const [value, setValue] = useState('')
  const [hits, setHits] = useState<CodeSearchHit[] | null>(null)
  const [upds, setUpds] = useState<UpdSearchHit[]>([])
  const [edoSyncedAt, setEdoSyncedAt] = useState<string | null>(null)
  const [unparsedOutgoing, setUnparsedOutgoing] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searchedCode, setSearchedCode] = useState('')
  const [history, setHistory] = useState<MarkHistoryResponse | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const requestId = useRef(0)

  useLayoutEffect(() => {
    requestId.current += 1
    if (open) {
      setValue('')
      setLoading(false)
      setHits(null)
      setUpds([])
      setEdoSyncedAt(null)
      setUnparsedOutgoing(0)
      setError(null)
      setSearchedCode('')
      setHistory(null)
      setHistoryLoading(false)
      setHistoryError(null)
      const timer = setTimeout(() => inputRef.current?.focus(), 0)
      return () => { clearTimeout(timer); requestId.current += 1 }
    }
  }, [open])

  useEffect(() => {
    if (!open || !searchedCode || !history?.refreshing) return
    const id = requestId.current
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const started = Date.now()
    const poll = async () => {
      let again = true
      try {
        const { data } = await markHistoryApi.get(searchedCode)
        if (stopped || id !== requestId.current) return
        setHistory(data)
        setHistoryError(null)
        again = data.refreshing
      } catch {
        if (stopped || id !== requestId.current) return
        setHistoryError('Не удалось прочитать обновление ЧЗ. Сохранённые сведения показаны ниже.')
      }
      if (again && !stopped && id === requestId.current) {
        if (Date.now() - started > 120000) {
          setHistory(previous => previous ? { ...previous, refreshing: false } : previous)
          setHistoryError('Обновление ЧЗ занимает больше времени. Повторите запрос позже.')
        } else timer = setTimeout(() => void poll(), 1500)
      }
    }
    timer = setTimeout(() => void poll(), 1000)
    return () => { stopped = true; clearTimeout(timer) }
  }, [open, searchedCode, history?.refreshing])

  const loadHistory = async (code: string, id: number, force = false) => {
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      const { data } = await markHistoryApi.refresh(code, force)
      if (id === requestId.current) setHistory(data)
    } catch (err) {
      if (id !== requestId.current) return
      const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
      setHistoryError(typeof detail === 'string' ? detail : 'Не удалось запустить обновление ЧЗ. Наши документы доступны отдельно.')
    } finally {
      if (id === requestId.current) setHistoryLoading(false)
    }
  }

  const runSearch = async (raw: string) => {
    const code = normalizeScannerInput(raw).trim()
    if (!code) return
    const id = ++requestId.current
    setLoading(true)
    setError(null)
    setEdoSyncedAt(null)
    setUnparsedOutgoing(0)
    setHits(null)
    setUpds([])
    setSearchedCode(code)
    setHistory(null)
    void loadHistory(code, id)
    try {
      const { data } = await scansApi.searchByCode(code)
      if (id !== requestId.current) return
      setHits(data.scans)
      setUpds(data.upds)
      setEdoSyncedAt(data.edo_synced_at ?? null)
      setUnparsedOutgoing(data.edo_unparsed_outgoing_documents ?? 0)
    } catch (e) {
      if (id !== requestId.current) return
      console.error('Code search error:', e)
      setError('Не удалось выполнить поиск. Попробуйте ещё раз.')
      setHits(null)
      setUpds([])
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }

  const scannedSearch = useRef<(code: string) => void>(() => {})
  scannedSearch.current = code => {
    setValue(normalizeScannerInput(code))
    void runSearch(code)
  }
  useLayoutEffect(() => {
    if (!open) return
    return captureSerialScans(code => scannedSearch.current(code))
  }, [open])

  if (!open) return null

  return (
    <div className="popup">
      <div className="popup__overlay" onClick={onClose} />
      <dialog className="popup__body" aria-label="Поиск марки и хронология" style={{ width: 'calc(100vw - 32px)', maxWidth: 1120, maxHeight: 'calc(100dvh - 32px)', overflowY: 'auto' }} open>
        <button
          type="button"
          className="popup__close"
          onClick={onClose}
          aria-label="Закрыть"
        />
        <div className="popup__title">Поиск марки и хронология</div>
        <div className="popup__content">
          <SerialScannerStatus serial={serial} />
          <div className="flex-row gap-8">
            <input
              ref={inputRef}
              value={value}
              onChange={(e) => setValue(normalizeScannerInput(e.target.value))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  void runSearch(inputRef.current?.value ?? value)
                }
              }}
              placeholder="Отсканируйте или введите код…"
              className="scan-input__field"
              autoComplete="off"
              spellCheck={false}
              style={{ flex: 1, minWidth: 0 }}
            />
            <button
              type="button"
              className="button"
              disabled={loading || !value.trim()}
              onClick={() => void runSearch(value)}
            >
              {loading ? 'Поиск…' : 'Найти'}
            </button>
          </div>

          {error && <p className="hint" style={{ color: 'var(--ms-error)' }}>{error}</p>}

          {searchedCode && <MarkChronology scans={hits || []} upds={upds} history={history}
            loading={historyLoading} documentsLoading={loading} error={historyError}
            onRefresh={() => void loadHistory(searchedCode, requestId.current, true)} />}

          <details className="mark-document-details">
            <summary>Документы списком{hits !== null ? ` (${hits.length + upds.length})` : ''}</summary>
          {hits !== null && !loading && (
            hits.length === 0 && upds.length === 0 ? (
              <p className="hint" style={{ marginTop: 12 }}>
                Марка не найдена в документах приложения и загруженных марках УПД.
              </p>
            ) : (
              <>
                {hits.length > 0 && (
                  <>
                    <div style={{ marginTop: 12, fontWeight: 600, fontSize: 13 }}>
                      В документах приложения
                    </div>
                    <div style={{ overflowX: 'auto' }}><table className="ui-table" style={{ marginTop: 6 }}>
                      <thead>
                        <tr>
                          <th>Документ</th>
                          <th>Тип</th>
                          <th>Статус марки</th>
                          <th>Время</th>
                        </tr>
                      </thead>
                      <tbody>
                        {hits.map((h) => {
                          const acceptedSupply = h.document_kind === 'supply' && h.document_status === 'accepted'
                          const acceptedMark = acceptedSupply && (h.accepted_without_cz || ['valid', 'overflow'].includes(h.status))
                          const st = acceptedMark
                            ? { label: h.accepted_without_cz ? 'Принято без проверки ЧЗ' : 'Принято', cls: 'badge--ok' }
                            : STATUS_LABELS[h.status] ?? { label: h.status, cls: 'badge--pending' }
                          return (
                            <tr key={h.scan_id} style={acceptedSupply ? { background: 'var(--st-ok-bg, #eef8ef)' } : undefined}>
                              <td>
                                {h.document_name}
                                {h.product_name ? (
                                  <div className="text-muted" style={{ fontSize: 11 }}>{h.product_name}</div>
                                ) : null}
                              </td>
                              <td>{KIND_LABELS[h.document_kind] ?? h.document_kind}</td>
                              <td><span className={`badge ${st.cls}`} style={acceptedSupply ? { whiteSpace: 'normal' } : undefined}>{st.label}</span></td>
                              <td className="text-muted" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                                {new Date(h.scanned_at).toLocaleString('ru')}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table></div>
                  </>
                )}

                {upds.length > 0 && (
                  <>
                    <div style={{ marginTop: 16, fontWeight: 600, fontSize: 13 }}>
                      Во входящих и исходящих УПД (ЭДО)
                    </div>
                    <div style={{ overflowX: 'auto' }}><table className="ui-table" style={{ marginTop: 6 }}>
                      <thead>
                        <tr>
                          <th>Направление</th>
                          <th>УПД</th>
                          <th>Контрагент</th>
                          <th>Статус УПД</th>
                          <th>Дата</th>
                        </tr>
                      </thead>
                      <tbody>
                        {upds.map((u, i) => (
                          <tr key={`${u.edo_document_id}-${i}`} style={isOutgoing(u.direction) ? { background: 'var(--st-warn-bg, #fff8e6)' } : undefined}>
                            <td>
                              <span className={`badge ${isIncoming(u.direction) ? 'badge--ok' : 'badge--warn'}`}>
                                {u.direction ?? '—'}
                              </span>
                            </td>
                            <td>
                              {u.doc_type ?? 'УПД'}
                              {u.number ? ` № ${u.number}` : ''}
                            </td>
                            <td>
                              {u.counterparty_name ?? '—'}
                              {u.counterparty_inn ? (
                                <div className="text-muted" style={{ fontSize: 11 }}>ИНН {u.counterparty_inn}</div>
                              ) : null}
                            </td>
                            <td>{u.state_name || 'Статус неизвестен'}</td>
                            <td className="text-muted" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                              {u.doc_date ?? '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table></div>
                  </>
                )}
              </>
            )
          )}
          {hits !== null && !loading && <div className="hint" style={{ marginTop: 12 }}>
            <p>Поиск по УПД использует марки, загруженные из ЭДО.{edoSyncedAt ? ` Последняя синхронизация: ${new Date(edoSyncedAt).toLocaleString('ru')}.` : ' Дата синхронизации недоступна.'}</p>
            {unparsedOutgoing > 0 && <p role="status">Исходящих УПД без загруженных марок: {unparsedOutgoing}. Поиск по этим документам пока неполон.</p>}
          </div>}
          </details>
        </div>
      </dialog>
    </div>
  )
}
