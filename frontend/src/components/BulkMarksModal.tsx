import { useMemo, useRef, useState } from 'react'
import type { MixedImportTarget } from '../api/client'
import { parseCodeList } from '../lib/parseCodeList'

interface Props {
  open: boolean
  onClose: () => void
  /** Загрузить распознанный список кодов. Бросает — модалка покажет ошибку. */
  onSubmit: (codes: string[], mixed: boolean, target: MixedImportTarget) => Promise<string | void>
  busy?: boolean
  title?: string
}

/**
 * Модалка ручной загрузки списка марок: вставить текст (по коду в строке) или
 * выбрать .txt/.csv файл. Общий компонент для отгрузки, приёмки и списания — родитель
 * решает, куда отправлять коды (onSubmit).
 */
export function BulkMarksModal({ open, onClose, onSubmit, busy = false, title }: Props) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [mixed, setMixed] = useState(true)
  const [target, setTarget] = useState<MixedImportTarget>('units')
  const [result, setResult] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<{ code: string; message: string }[]>([])
  const fileRef = useRef<HTMLInputElement>(null)

  const codes = useMemo(() => parseCodeList(text), [text])

  if (!open) return null

  const handleFile = async (file: File | null) => {
    if (!file) return
    try {
      setResult(null)
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
    setConflicts([])
    setResult(null)
    try {
      const message = await onSubmit(codes, mixed, target)
      setText('')
      if (message) setResult(message)
      else onClose()
    } catch (e) {
      const ax = e as { message?: string; conflicts?: { code: string; message: string }[]; response?: { data?: { detail?: string } } }
      setError(ax?.response?.data?.detail ?? (/[А-Яа-яЁё]/.test(ax.message ?? '') ? ax.message! : 'Не удалось загрузить список марок'))
      setConflicts(ax.conflicts ?? [])
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
          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 8 }}>
            <input type="checkbox" checked={mixed} disabled={busy}
              onChange={e => { setMixed(e.target.checked); setError(null); setConflicts([]); setResult(null) }} />
            <span>Смешанный список: короб, блоки и пачки</span>
          </label>
          {mixed && <>
            <label className="field-label">Оставить в документе
              <select className="ui-select" value={target} disabled={busy} onChange={e => {
                setTarget(e.target.value as MixedImportTarget); setError(null); setConflicts([]); setResult(null)
              }}>
                <option value="units">Только пачки</option>
                <option value="blocks">Только блоки</option>
              </select>
            </label>
            <p className="hint">Коды коробов исключаются из результата. {target === 'blocks'
              ? 'Останутся коды блоков, а входящие в них пачки будут исключены. Для каждой пачки должен быть подтверждённый блок в списке или документе.'
              : 'Останутся отдельные марки пачек; коды блоков будут исключены.'} Повторы учитываются один раз. При неизвестном составе документ не меняется. Для получения состава нужен вход в ЧЗ.</p>
          </>}
          {busy && mixed && <p role="status">Получаем состав упаковок в ЧЗ… Можно подождать здесь или вернуться к документу после перезагрузки страницы.</p>}
          {result && <p role="status" className="alert alert--success">{result}</p>}
          <textarea
            value={text}
            onChange={(e) => { setText(e.target.value); setResult(null) }}
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
            <div role="alert" className="alert alert--error" style={{ marginTop: 10 }}>
              {error}
              {conflicts.length > 0 && <ul style={{ maxHeight: 200, overflowY: 'auto' }}>
                {conflicts.map((conflict, i) => <li key={i}>
                  {conflict.code && <code style={{ overflowWrap: 'anywhere' }}>{conflict.code}: </code>}{conflict.message}
                </li>)}
              </ul>}
            </div>
          )}
        </div>
        <div className="buttons" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
          <button type="button" className="button" onClick={onClose} disabled={busy}>
            {result ? 'Готово' : 'Отмена'}
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
