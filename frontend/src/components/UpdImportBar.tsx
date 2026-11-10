import { useEffect, useState } from 'react'
import { acceptanceApi } from '../api/client'
import type { AcceptanceDoc, ProductGroup, MsDocument } from '../api/client'
import { useMsDocuments } from '../hooks/useDocuments'
import { BulkMarksModal } from './BulkMarksModal'
import { EdoImportModal } from './EdoImportModal'
import { useEdition } from '../hooks/useEdition'

interface UpdImportBarProps {
  busy: boolean
  imported: AcceptanceDoc | null
  onNew: () => void
  onSubmit: (file: File, productGroup: string, moyskladId: string) => void
  /** Загрузить список марок вручную (альтернатива УПД) с выбранной группой/поступлением. */
  onSubmitMarks: (codes: string[], productGroup: string, moyskladId: string) => Promise<void>
  /** Принять входящий УПД из ЭДО (Saby) с выбранной группой/поступлением. */
  onSubmitEdo: (externalId: string, productGroup: string, moyskladId: string) => Promise<void>
  /** Попап из кнопки МС: поступление задано заранее — прячем поиск/выбор поступления. */
  presetMoyskladId?: string
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
export function UpdImportBar({ busy, imported, onNew, onSubmit, onSubmitMarks, onSubmitEdo, presetMoyskladId }: UpdImportBarProps) {
  const [source, setSource] = useState('xml')
  const [groups, setGroups] = useState<ProductGroup[]>([])
  const [group, setGroup] = useState('')
  const [supply, setSupply] = useState<MsDocument | null>(null)
  const moyskladId = presetMoyskladId || supply?.id || ''
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [marksOpen, setMarksOpen] = useState(false)
  const [edoOpen, setEdoOpen] = useState(false)
  // ЭДО (Saby) — только полная версия; в ms_lite приёмка идёт ручным импортом УПД-XML.
  const { isFull, isLoading: editionLoading } = useEdition()

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

  const options = supplies ?? []
  const visibleSupplies = supply && !options.some(item => item.id === supply.id) ? [supply, ...options] : options
  if (imported) return (
    <div className="upd-import-result">
      <div>
        <strong>{imported.name}</strong>
        <div>{imported.moysklad_id
          ? `Поступление МойСклад · ${supply ? msSupplyLabel(supply) : 'Текущее поступление'}`
          : 'Поступление МойСклад не выбрано'}</div>
        {!imported.moysklad_id && <p className="hint">Можно сопоставить товары. Эта приёмка не будет отправлена в МойСклад.</p>}
      </div>
      <div className="flex-row gap-8">
        {imported.status === 'draft' && <button className="button button--sm" disabled={busy}
          onClick={() => setMarksOpen(true)}>Добавить марки</button>}
        <button className="button button--sm" disabled={busy} onClick={onNew}>Новая приёмка</button>
      </div>
      <BulkMarksModal open={marksOpen} onClose={() => setMarksOpen(false)} busy={busy}
        onSubmit={codes => onSubmitMarks(codes, imported.product_group || group, imported.moysklad_id || '')} />
    </div>
  )

  return (<>
    <fieldset className="upd-source" disabled={busy}>
      <legend className="field-label">Источник приёмки</legend>
      {[['xml', 'Файл УПД'], ['marks', 'Список марок'], ...(isFull && !editionLoading ? [['edo', 'Из ЭДО']] : [])].map(([value, label]) =>
        <label key={value}><input type="radio" name="acceptance-source" checked={source === value}
          onChange={() => { setSource(value); setFile(null) }} /> {label}</label>)}
    </fieldset>
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

      {presetMoyskladId ? (
        // Попап из кнопки МС: поступление уже выбрано (документ, из которого открыли окно).
        <div className="upd-bar__field">
          <label className="field-label">Поступление в МойСклад</label>
          <div className="badge badge--ok" style={{ alignSelf: 'flex-start' }}>
            Запись в текущее поступление
          </div>
        </div>
      ) : (
        <div className="upd-bar__field">
          <label className="field-label" htmlFor="upd-supply">
            Поступление в МойСклад
          </label>
          <input
            className="ui-input ui-input--block"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск по номеру или поставщику…"
            aria-label="Найти поступление по номеру или поставщику"
            disabled={busy}
            style={{ marginBottom: 6 }}
          />
          <select
            id="upd-supply"
            className="ui-select"
            value={moyskladId}
            onChange={(e) => setSupply(visibleSupplies.find(item => item.id === e.target.value) ?? null)}
            aria-describedby={!moyskladId ? 'upd-supply-hint' : undefined}
            disabled={busy}
          >
            <option value="">
              {suppliesLoading
                ? 'Загружаю поступления…'
                : 'Без записи в МойСклад'}
            </option>
            {visibleSupplies.map((m) => (
              <option key={m.id} value={m.id}>
                {msSupplyLabel(m)}
              </option>
            ))}
          </select>
          {!moyskladId && <p id="upd-supply-hint" className="hint">Можно загрузить и сопоставить товары. Для отправки выберите поступление до загрузки.</p>}
        </div>
      )}

      {source === 'xml' && <><div className="upd-bar__field">
        <label className="field-label" htmlFor="upd-file">
          Файл УПД (XML 5.03)
        </label>
        <input
          id="upd-file"
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
        {busy ? 'Загрузка…' : 'Загрузить УПД'}
      </button></>}

      {source === 'marks' && <button
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
        Ввести список марок
      </button>}

      {source === 'edo' && isFull && (
        <button
          type="button"
          className="button"
          onClick={() => setEdoOpen(true)}
          disabled={!group || busy}
          title={
            group
              ? 'Принять входящий УПД из ЭДО (Saby)'
              : 'Сначала выберите товарную группу'
          }
        >
          Выбрать входящий УПД
        </button>
      )}

      <BulkMarksModal
        open={marksOpen}
        onClose={() => setMarksOpen(false)}
        busy={busy}
        onSubmit={(codes) => onSubmitMarks(codes, group, moyskladId)}
      />

      {isFull && (
        <EdoImportModal
          open={edoOpen}
          onClose={() => setEdoOpen(false)}
          groupSelected={!!group}
          busy={busy}
          onPick={(externalId) => onSubmitEdo(externalId, group, moyskladId)}
        />
      )}
    </div>
  </>)
}
