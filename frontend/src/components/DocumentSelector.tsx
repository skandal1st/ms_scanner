import { useState } from 'react'
import { useMsSupplies, useDocuments, useCreateDocument } from '../hooks/useDocuments'
import type { Document } from '../api/client'

interface Props {
  onSelect: (doc: Document) => void
  selected: Document | null
}

export function DocumentSelector({ onSelect, selected }: Props) {
  const [mode, setMode] = useState<'select' | 'create'>('select')
  const [newName, setNewName] = useState('')
  const [selectedMsId, setSelectedMsId] = useState('')

  const { data: supplies, isLoading: suppliesLoading } = useMsSupplies()
  const { data: documents } = useDocuments()
  const createMutation = useCreateDocument()

  const handleCreate = async () => {
    if (!newName.trim()) return
    const doc = await createMutation.mutateAsync({
      name: newName.trim(),
      moysklad_id: selectedMsId || undefined,
    })
    setNewName('')
    setSelectedMsId('')
    onSelect(doc)
  }

  return (
    <div>
      <div className="section__head">
        <span className="field-label" style={{ margin: 0 }}>Документ</span>
        <ul className="tabs__buttons" style={{ margin: 0 }}>
          <li
            className={`tabs__button ${mode === 'select' ? 'b-active' : ''}`}
            onClick={() => setMode('select')}
          >
            Выбрать
          </li>
          <li
            className={`tabs__button ${mode === 'create' ? 'b-active' : ''}`}
            onClick={() => setMode('create')}
          >
            Создать
          </li>
        </ul>
      </div>

      {selected && (
        <div className="badge badge--info mb-8" style={{ display: 'flex' }}>
          {selected.name}
          <span className="text-muted" style={{ marginLeft: 6 }}>({selected.status})</span>
        </div>
      )}

      {mode === 'select' && (
        <>
          {documents && documents.length > 0 ? (
            <div className="doc-list">
              {documents.map((doc) => (
                <button
                  key={doc.id}
                  type="button"
                  className={`doc-list__item ${selected?.id === doc.id ? 'is-active' : ''}`}
                  onClick={() => onSelect(doc)}
                >
                  <span>{doc.name}</span>
                  <span className="doc-list__item-count">{doc.scan_count} кодов</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="hint">Нет документов</p>
          )}
        </>
      )}

      {mode === 'create' && (
        <div className="login-form">
          {!suppliesLoading && supplies && supplies.length > 0 && (
            <select
              className="ui-select"
              style={{ minWidth: 0, width: '100%' }}
              value={selectedMsId}
              onChange={(e) => {
                setSelectedMsId(e.target.value)
                const s = supplies.find((x) => x.id === e.target.value)
                if (s) setNewName(s.name)
              }}
            >
              <option value="">Привязать поступление из МойСклад…</option>
              {supplies.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          )}
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Название документа"
            className="ui-input ui-input--block"
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
          />
          <button
            type="button"
            className="button button--success"
            onClick={handleCreate}
            disabled={!newName.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? 'Создаю…' : 'Создать'}
          </button>
        </div>
      )}
    </div>
  )
}
