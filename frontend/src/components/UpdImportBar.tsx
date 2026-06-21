import { useEffect, useRef, useState } from 'react'
import { acceptanceApi } from '../api/client'
import type { ProductGroup } from '../api/client'

interface UpdImportBarProps {
  busy: boolean
  onSubmit: (file: File, productGroup: string) => void
}

/**
 * Панель загрузки УПД: выбор товарной группы (обязателен) + выбор XML-файла +
 * кнопка «Загрузить». Сам импорт выполняет родитель через onSubmit.
 */
export function UpdImportBar({ busy, onSubmit }: UpdImportBarProps) {
  const [groups, setGroups] = useState<ProductGroup[]>([])
  const [group, setGroup] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    acceptanceApi
      .productGroups()
      .then(({ data }) => setGroups(data))
      .catch(() => setGroups([]))
  }, [])

  const submit = () => {
    if (!group || !file || busy) return
    onSubmit(file, group)
  }

  return (
    <div className="upd-bar">
      <div className="upd-bar__field">
        <label className="field-label" htmlFor="upd-group">
          Тип маркированной продукции
        </label>
        <select
          id="upd-group"
          className="ui-select"
          value={group}
          onChange={(e) => setGroup(e.target.value)}
          disabled={busy}
        >
          <option value="">— выберите товарную группу —</option>
          {groups.map((g) => (
            <option key={g.code} value={g.code}>
              {g.label}
            </option>
          ))}
        </select>
      </div>

      <div className="upd-bar__field">
        <label className="field-label" htmlFor="upd-file">
          Файл УПД (XML 5.03)
        </label>
        <input
          id="upd-file"
          ref={fileRef}
          type="file"
          accept=".xml,text/xml,application/xml"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          disabled={busy || !group}
        />
      </div>

      <button
        type="button"
        className="button button--success"
        onClick={submit}
        disabled={!group || !file || busy}
      >
        {busy ? 'Загрузка…' : 'Загрузить'}
      </button>
    </div>
  )
}
