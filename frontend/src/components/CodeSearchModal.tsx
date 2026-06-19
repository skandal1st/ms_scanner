import { useEffect, useRef, useState } from 'react'
import { scansApi, type CodeSearchHit } from '../api/client'
import { normalizeScannerInput } from '../lib/scannerLayout'

type Props = {
  open: boolean
  onClose: () => void
}

const KIND_LABELS: Record<string, string> = {
  supply: 'Приёмка',
  demand: 'Отгрузка',
  loss: 'Списание',
  salesreturn: 'Возврат',
  move: 'Перемещение',
}

const STATUS_LABELS: Record<string, { label: string; cls: string }> = {
  pending: { label: 'Проверяется', cls: 'badge--pending' },
  valid: { label: 'Валиден', cls: 'badge--ok' },
  invalid: { label: 'Ошибка', cls: 'badge--error' },
  duplicate: { label: 'Дубль', cls: 'badge--warn' },
  overflow: { label: 'Сверх плана', cls: 'badge--error' },
  unknown_product: { label: 'Нет товара', cls: 'badge--warn' },
  used_in_other_doc: { label: 'В другом документе', cls: 'badge--warn' },
}

export function CodeSearchModal({ open, onClose }: Props) {
  const [value, setValue] = useState('')
  const [hits, setHits] = useState<CodeSearchHit[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setValue('')
      setHits(null)
      setError(null)
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [open])

  const runSearch = async (raw: string) => {
    const code = raw.trim()
    if (!code) return
    setLoading(true)
    setError(null)
    try {
      const { data } = await scansApi.searchByCode(code)
      setHits(data)
    } catch (e) {
      console.error('Code search error:', e)
      setError('Не удалось выполнить поиск. Попробуйте ещё раз.')
      setHits(null)
    } finally {
      setLoading(false)
    }
  }

  if (!open) return null

  return (
    <div className="popup">
      <div className="popup__overlay" onClick={onClose} />
      <dialog className="popup__body" open>
        <button
          type="button"
          className="popup__close"
          onClick={onClose}
          aria-label="Закрыть"
        />
        <div className="popup__title">Поиск марки по документам</div>
        <div className="popup__content">
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
              style={{ flex: 1 }}
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

          {hits !== null && !loading && (
            hits.length === 0 ? (
              <p className="hint" style={{ marginTop: 12 }}>
                Марка не найдена ни в одном документе.
              </p>
            ) : (
              <table className="ui-table" style={{ marginTop: 12 }}>
                <thead>
                  <tr>
                    <th>Документ</th>
                    <th>Тип</th>
                    <th>Статус</th>
                    <th>Время</th>
                  </tr>
                </thead>
                <tbody>
                  {hits.map((h) => {
                    const st = STATUS_LABELS[h.status] ?? { label: h.status, cls: 'badge--pending' }
                    return (
                      <tr key={h.scan_id}>
                        <td>
                          {h.document_name}
                          {h.product_name ? (
                            <div className="text-muted" style={{ fontSize: 11 }}>{h.product_name}</div>
                          ) : null}
                        </td>
                        <td>{KIND_LABELS[h.document_kind] ?? h.document_kind}</td>
                        <td><span className={`badge ${st.cls}`}>{st.label}</span></td>
                        <td className="text-muted" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                          {new Date(h.scanned_at).toLocaleString('ru')}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )
          )}
        </div>
      </dialog>
    </div>
  )
}
