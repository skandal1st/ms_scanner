import { useState } from 'react'
import { useMsDocuments, useDocuments, useCreateDocument } from '../hooks/useDocuments'
import type { Document, DocumentKind } from '../api/client'

interface Props {
  kind: DocumentKind
  onSelect: (doc: Document) => void
  selected: Document | null
}

const KIND_LABEL: Record<DocumentKind, string> = {
  demand: 'отгрузку',
}

export function DocumentSelector({ kind, onSelect, selected }: Props) {
  const [mode, setMode] = useState<'select' | 'create'>('select')
  const [newName, setNewName] = useState('')
  const [selectedMsId, setSelectedMsId] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)

  const { data: msDocs, isLoading: msLoading } = useMsDocuments(kind)
  const { data: documents } = useDocuments(kind)
  const createMutation = useCreateDocument()

  const errorText = (err: any, fallback: string) => {
    const detail = err?.response?.data?.detail
    const status = err?.response?.status
    if (typeof detail === 'string') return detail
    if (status) return `Ошибка ${status}: ${fallback}`
    return fallback
  }

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreateError(null)
    try {
      const doc = await createMutation.mutateAsync({
        name: newName.trim(),
        kind,
        moysklad_id: selectedMsId || undefined,
      })
      setNewName('')
      setSelectedMsId('')
      onSelect(doc)
    } catch (err: any) {
      setCreateError(errorText(err, 'Не удалось создать документ'))
    }
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

      {mode === 'select' && (() => {
        const boundMsIds = new Set(
          (documents ?? []).map((d) => d.moysklad_id).filter(Boolean) as string[],
        )
        const unboundMs = (msDocs ?? []).filter((m) => !boundMsIds.has(m.id))
        const isEmpty = (!documents || documents.length === 0) && unboundMs.length === 0

        const handleBindMs = async (msId: string, msName: string) => {
          setCreateError(null)
          try {
            const doc = await createMutation.mutateAsync({
              name: msName || `МС ${msId.slice(0, 8)}`,
              kind,
              moysklad_id: msId,
            })
            onSelect(doc)
          } catch (err: any) {
            setCreateError(errorText(err, 'Не удалось привязать документ'))
          }
        }

        if (isEmpty) {
          return (
            <p className="hint">
              {msLoading ? 'Загружаю документы из МойСклад...' : 'Нет документов'}
            </p>
          )
        }

        return (
          <div className="doc-list">
            {(documents ?? []).map((doc) => (
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
            {unboundMs.map((m) => (
              <button
                key={`ms-${m.id}`}
                type="button"
                className="doc-list__item"
                onClick={() => handleBindMs(m.id, m.name)}
                disabled={createMutation.isPending}
                title="Привязать документ из МойСклад"
              >
                <span>{m.name || `Без имени · ${m.id.slice(0, 8)}`}</span>
                <span className="doc-list__item-count">+ привязать</span>
              </button>
            ))}
            {createError && (
              <div
                style={{
                  marginTop: 8,
                  padding: 8,
                  background: '#fef2f2',
                  border: '1px solid #fecaca',
                  borderRadius: 6,
                  color: '#b91c1c',
                  fontSize: 12,
                }}
              >
                {createError}
              </div>
            )}
          </div>
        )
      })()}

      {mode === 'create' && (
        <div className="login-form">
          {!msLoading && msDocs && msDocs.length > 0 && (
            <select
              className="ui-select"
              style={{ minWidth: 0, width: '100%' }}
              value={selectedMsId}
              onChange={(e) => {
                setSelectedMsId(e.target.value)
                const s = msDocs.find((x) => x.id === e.target.value)
                if (s) setNewName(s.name)
              }}
            >
              <option value="">Привязать {KIND_LABEL[kind]} из МойСклад...</option>
              {msDocs.map((s) => (
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
            {createMutation.isPending ? 'Создаю...' : 'Создать'}
          </button>
          {createError && (
            <div
              style={{
                padding: 8,
                background: '#fef2f2',
                border: '1px solid #fecaca',
                borderRadius: 6,
                color: '#b91c1c',
                fontSize: 12,
              }}
            >
              {createError}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
