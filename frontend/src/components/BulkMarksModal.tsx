import { useMemo, useRef, useState } from 'react'
import { parseCodeList } from '../lib/parseCodeList'

interface Props {
  open: boolean
  onClose: () => void
  /** Загрузить распознанный список кодов. Бросает — модалка покажет ошибку. */
  onSubmit: (codes: string[]) => Promise<void>
  busy?: boolean
  title?: string
}

/**
 * Модалка ручной загрузки списка марок: вставить текст (по коду в строке) или
 * выбрать .txt/.csv файл. Общий компонент для отгрузки и приёмки — родитель
 * решает, куда отправлять коды (onSubmit).
 */
export function BulkMarksModal({ open, onClose, onSubmit, busy = false, title }: Props) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const codes = useMemo(() => parseCodeList(text), [text])

  if (!open) return null

  const handleFile = async (file: File | null) => {
    if (!file) return
    try {
      const content = await file.text()
      // Дописываем к уже введённому — можно склеить несколько файлов/вставок.
      setText((prev) => (prev ? `${prev}\n${content}` : content))
    } catch {
      setError('Не удалось прочитать файл')
    }
    if (fileRef.current) fileRef.current.value = ''
  }

  const submit = async () => {
    if (!codes.length || busy) return
    setError(null)
    try {
      await onSubmit(codes)
      setText('')
      onClose()
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setError(ax?.response?.data?.detail ?? 'Не удалось загрузить список марок')
    }
  }

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
        <div className="popup__title">{title ?? 'Загрузить список марок'}</div>
        <div className="popup__content">
          <p className="hint" style={{ marginTop: 0 }}>
            Вставьте коды (по одному в строке) или выберите файл .txt / .csv.
          </p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="0104603312830412215abcd…&#10;0104603312830412215efgh…"
            disabled={busy}
            rows={10}
            className="ui-input ui-input--block"
            style={{
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: 12,
              resize: 'vertical',
              width: '100%',
            }}
            spellCheck={false}
            autoComplete="off"
          />
          <div className="flex-row gap-8" style={{ alignItems: 'center', marginTop: 8 }}>
            <input
              ref={fileRef}
              type="file"
              accept=".txt,.csv,text/plain,text/csv"
              onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
              disabled={busy}
            />
            <span className="text-muted" style={{ fontSize: 12, marginLeft: 'auto' }}>
              Распознано кодов: <b>{codes.length}</b>
            </span>
          </div>
          {error && (
            <div className="alert alert--error" style={{ marginTop: 10 }}>
              {error}
            </div>
          )}
        </div>
        <div className="buttons" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
          <button type="button" className="button" onClick={onClose} disabled={busy}>
            Отмена
          </button>
          <button
            type="button"
            className="button button--success"
            onClick={submit}
            disabled={!codes.length || busy}
          >
            {busy ? 'Загрузка…' : `Загрузить ${codes.length} марок`}
          </button>
        </div>
      </dialog>
    </div>
  )
}
