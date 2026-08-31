import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { supportApi } from '../api/client'

interface Props {
  open: boolean
  onClose: () => void
  /** Значение категории по умолчанию (например, раздел, откуда открыли). */
  defaultCategory?: string
  /** Метка страницы/раздела — уходит в тикет как контекст для поддержки. */
  page?: string
}

/**
 * Модалка «Написать в техподдержку» — создаёт тикет в AXIMA ERP через
 * POST /support/ticket. Общий компонент: вызывается со страницы помощи и из навигации.
 */
export function SupportModal({ open, onClose, defaultCategory = 'general', page }: Props) {
  const [category, setCategory] = useState(defaultCategory)
  const [subject, setSubject] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [okRef, setOkRef] = useState<string | null | undefined>(undefined)

  const { data: categories } = useQuery({
    queryKey: ['support-categories'],
    queryFn: () => supportApi.categories().then((r) => r.data),
    enabled: open,
    staleTime: 5 * 60_000,
  })

  // Сброс состояния при каждом открытии.
  useEffect(() => {
    if (open) {
      setCategory(defaultCategory)
      setSubject('')
      setMessage('')
      setError(null)
      setOkRef(undefined)
      setBusy(false)
    }
  }, [open, defaultCategory])

  if (!open) return null

  const submit = async () => {
    if (busy) return
    if (subject.trim().length < 3 || message.trim().length < 5) {
      setError('Заполните тему (от 3 символов) и опишите проблему (от 5 символов).')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { data } = await supportApi.createTicket({
        subject: subject.trim(),
        message: message.trim(),
        category,
        page,
      })
      setOkRef(data.ref ?? null)
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setError(ax?.response?.data?.detail ?? 'Не удалось отправить обращение. Попробуйте позже.')
    } finally {
      setBusy(false)
    }
  }

  const sent = okRef !== undefined

  return (
    <div className="popup">
      <div className="popup__overlay" onClick={busy ? undefined : onClose} />
      <dialog className="popup__body" open>
        <button
          type="button"
          className="popup__close"
          onClick={onClose}
          disabled={busy}
          aria-label="Закрыть"
        />
        <div className="popup__title">Написать в техподдержку</div>
        <div className="popup__content">
          {sent ? (
            <div className="alert alert--ok" style={{ marginTop: 0 }}>
              <div>
                <b>Обращение отправлено.</b>
                {okRef ? (
                  <> Номер тикета: <b>{okRef}</b>. Мы ответим на вашу почту.</>
                ) : (
                  <> Мы ответим на вашу почту.</>
                )}
              </div>
            </div>
          ) : (
            <>
              <label className="field-label" htmlFor="support-category">
                Раздел
              </label>
              <select
                id="support-category"
                className="ui-input ui-input--block"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                disabled={busy}
              >
                {(categories ?? [{ value: 'general', label: 'Другое' }]).map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>

              <label className="field-label" htmlFor="support-subject" style={{ marginTop: 12 }}>
                Тема
              </label>
              <input
                id="support-subject"
                className="ui-input ui-input--block"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="Коротко о проблеме"
                maxLength={200}
                disabled={busy}
              />

              <label className="field-label" htmlFor="support-message" style={{ marginTop: 12 }}>
                Опишите проблему
              </label>
              <textarea
                id="support-message"
                className="ui-input ui-input--block"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Что произошло, на каком шаге, что ожидали увидеть. По возможности — номер документа."
                rows={6}
                maxLength={5000}
                disabled={busy}
                style={{ resize: 'vertical' }}
              />

              {error && (
                <div className="alert alert--error" style={{ marginTop: 10 }}>
                  {error}
                </div>
              )}
            </>
          )}
        </div>
        <div className="buttons" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
          {sent ? (
            <button type="button" className="button button--success" onClick={onClose}>
              Закрыть
            </button>
          ) : (
            <>
              <button type="button" className="button" onClick={onClose} disabled={busy}>
                Отмена
              </button>
              <button
                type="button"
                className="button button--success"
                onClick={submit}
                disabled={busy}
              >
                {busy ? 'Отправка…' : 'Отправить'}
              </button>
            </>
          )}
        </div>
      </dialog>
    </div>
  )
}
