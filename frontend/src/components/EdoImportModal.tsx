import { useEffect, useState } from 'react'
import { acceptanceApi } from '../api/client'
import type { EdoIncomingDoc } from '../api/client'

interface Props {
  open: boolean
  onClose: () => void
  /** Выбрана ли товарная группа в панели (без неё импорт невозможен). */
  groupSelected: boolean
  busy?: boolean
  /** Принять выбранный входящий УПД. Бросает — модалка покажет ошибку. */
  onPick: (externalId: string) => Promise<void>
}

/**
 * Модалка приёмки из ЭДО: список входящих УПД (Поступление) из Saby. По кнопке
 * «Принять» родитель скачивает XML и создаёт приёмку с выбранной группой/поступлением.
 */
export function EdoImportModal({ open, onClose, groupSelected, busy = false, onPick }: Props) {
  const [docs, setDocs] = useState<EdoIncomingDoc[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [picking, setPicking] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    setLoading(true)
    acceptanceApi
      .edoIncoming()
      .then(({ data }) => setDocs(data))
      .catch((e) => {
        const ax = e as { response?: { data?: { detail?: string } } }
        setError(ax?.response?.data?.detail ?? 'Не удалось получить входящие документы ЭДО')
        setDocs([])
      })
      .finally(() => setLoading(false))
  }, [open])

  if (!open) return null

  const pick = async (ext: string) => {
    if (busy || picking) return
    setError(null)
    setPicking(ext)
    try {
      await onPick(ext)
      onClose()
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setError(ax?.response?.data?.detail ?? 'Не удалось принять УПД из ЭДО')
    } finally {
      setPicking(null)
    }
  }

  return (
    <div className="popup">
      <div className="popup__overlay" onClick={busy ? undefined : onClose} />
      <dialog className="popup__body" open style={{ maxWidth: 720 }}>
        <button
          type="button"
          className="popup__close"
          onClick={onClose}
          disabled={busy}
          aria-label="Закрыть"
        />
        <div className="popup__title">Приёмка из ЭДО — входящие УПД</div>
        <div className="popup__content">
          {!groupSelected && (
            <div className="alert alert--error" style={{ marginTop: 0, marginBottom: 10 }}>
              Сначала выберите товарную группу в панели приёмки.
            </div>
          )}
          <p className="hint" style={{ marginTop: 0 }}>
            Входящие поступления из Saby с первичным УПД. Нажмите «Принять» — коды из
            документа загрузятся в новую приёмку.
          </p>
          {loading && <div className="text-muted">Загружаю входящие документы…</div>}
          {error && (
            <div className="alert alert--error" style={{ marginTop: 10 }}>
              {error}
            </div>
          )}
          {!loading && !error && docs.length === 0 && (
            <div className="text-muted">Входящих УПД за период не найдено.</div>
          )}
          {docs.length > 0 && (
            <div style={{ maxHeight: 360, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
              {docs.map((d) => (
                <div
                  key={d.external_id}
                  className="flex-row gap-8"
                  style={{
                    alignItems: 'center',
                    padding: '8px 10px',
                    border: '1px solid var(--bd, #d0d0d0)',
                    borderRadius: 'var(--r-md, 8px)',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>
                      УПД № {d.number ?? '—'}
                      {d.date ? ` от ${d.date}` : ''}
                    </div>
                    <div className="text-muted" style={{ fontSize: 12 }}>
                      {d.counterparty_name ?? 'Поставщик не указан'}
                      {d.counterparty_inn ? ` · ИНН ${d.counterparty_inn}` : ''}
                      {d.state_name ? ` · ${d.state_name}` : ''}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="button button--success button--sm"
                    disabled={!groupSelected || busy || picking !== null}
                    onClick={() => pick(d.external_id)}
                    style={{ whiteSpace: 'nowrap' }}
                  >
                    {picking === d.external_id ? 'Принимаю…' : 'Принять'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="buttons" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
          <button type="button" className="button" onClick={onClose} disabled={busy}>
            Закрыть
          </button>
        </div>
      </dialog>
    </div>
  )
}
