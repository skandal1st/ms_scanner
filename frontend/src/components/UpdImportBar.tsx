import { useEffect, useRef, useState } from 'react'
import { acceptanceApi } from '../api/client'
import type { ProductGroup, MsDocument } from '../api/client'
import { useMsDocuments } from '../hooks/useDocuments'
import { BulkMarksModal } from './BulkMarksModal'
import { Icon } from './Icon'

interface UpdImportBarProps {
  busy: boolean
  onSubmit: (file: File, productGroup: string, moyskladId: string) => void
  /** Загрузить список марок вручную (альтернатива УПД) с выбранной группой/поступлением. */
  onSubmitMarks: (codes: string[], productGroup: string, moyskladId: string) => Promise<void>
}

/** Отображаемое имя поступления МС: "00123 — ООО Поставщик". */
function msSupplyLabel(m: MsDocument): string {
  const number = m.name || `Без имени · ${m.id.slice(0, 8)}`
  return m.agent_name ? `${number} — ${m.agent_name}` : number
}

/**
 * Панель загрузки УПД: товарная группа (обязательна) + поступление МС (опционально —
 * куда писать КМ) + XML-файл + кнопка «Загрузить». Сам импорт выполняет родитель
 * через onSubmit.
 */
export function UpdImportBar({ busy, onSubmit, onSubmitMarks }: UpdImportBarProps) {
  const [groups, setGroups] = useState<ProductGroup[]>([])
  const [group, setGroup] = useState('')
  const [moyskladId, setMoyskladId] = useState('')
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [marksOpen, setMarksOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    acceptanceApi
      .productGroups()
      .then(({ data }) => setGroups(data))
      .catch(() => setGroups([]))
  }, [])

  // Серверный поиск поступлений в МС: дебаунс 300мс.
  useEffect(() => {
    const trimmed = search.trim()
    const handle = window.setTimeout(() => {
      setDebounced(trimmed.length >= 2 ? trimmed : '')
    }, 300)
    return () => window.clearTimeout(handle)
  }, [search])

  const { data: supplies, isLoading: suppliesLoading } = useMsDocuments(
    'supply',
    debounced || undefined,
  )

  const submit = () => {
    if (!group || !file || busy) return
    onSubmit(file, group, moyskladId)
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
        <label className="field-label" htmlFor="upd-supply">
          Поступление в МойСклад (куда записать КМ)
        </label>
        <input
          className="ui-input ui-input--block"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск по номеру или поставщику…"
          disabled={busy}
          style={{ marginBottom: 6 }}
        />
        <select
          id="upd-supply"
          className="ui-select"
          value={moyskladId}
          onChange={(e) => setMoyskladId(e.target.value)}
          disabled={busy}
        >
          <option value="">
            {suppliesLoading
              ? 'Загружаю поступления…'
              : '— без записи в МС (только проверка) —'}
          </option>
          {(supplies ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {msSupplyLabel(m)}
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

      <button
        type="button"
        className="button"
        onClick={() => setMarksOpen(true)}
        disabled={!group || busy}
        title={
          group
            ? 'Загрузить список марок вручную (без УПД)'
            : 'Сначала выберите товарную группу'
        }
      >
        <Icon name="upload" size={15} /> Список марок
      </button>

      <BulkMarksModal
        open={marksOpen}
        onClose={() => setMarksOpen(false)}
        busy={busy}
        onSubmit={(codes) => onSubmitMarks(codes, group, moyskladId)}
      />
    </div>
  )
}
