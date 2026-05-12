import { useState, useEffect } from 'react'
import { useScanStore } from '../store/scanStore'
import type { CSSProperties } from 'react'

/** Документ без плана МС: явный UUID товара для следующих сканов. */
export function ManualProductTargetBar() {
  const document = useScanStore((s) => s.document)
  const targetProductId = useScanStore((s) => s.targetProductId)
  const setTargetProductId = useScanStore((s) => s.setTargetProductId)
  const [draft, setDraft] = useState('')

  const hasPlan = (document?.plan?.length ?? 0) > 0

  useEffect(() => {
    setDraft(targetProductId ?? '')
  }, [targetProductId, document?.id])

  if (!document || hasPlan) return null

  return (
    <div style={styles.wrap}>
      <span style={styles.label}>Товар в МойСклад для сканов (если нет плана)</span>
      <div style={styles.row}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="UUID товара из номенклатуры"
          spellCheck={false}
          autoComplete="off"
          style={styles.input}
        />
        <button
          type="button"
          className="button"
          onClick={() => setTargetProductId(draft.trim() || null)}
        >
          Применить
        </button>
        {targetProductId ? (
          <button type="button" className="button" onClick={() => setTargetProductId(null)}>
            Авто
          </button>
        ) : null}
      </div>
      {targetProductId ? (
        <div style={styles.hint}>
          Активно: <code>{targetProductId}</code>
        </div>
      ) : (
        <div style={styles.hint}>Без UUID коды привязываются только по GTIN в каталоге МС.</div>
      )}
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  wrap: {
    marginBottom: 12,
    padding: 10,
    background: '#f8fafc',
    border: '1px solid #e2e8f0',
    borderRadius: 8,
  },
  label: {
    display: 'block',
    fontSize: 12,
    fontWeight: 600,
    color: '#334155',
    marginBottom: 8,
  },
  row: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 8,
    alignItems: 'center',
  },
  input: {
    flex: '1 1 200px',
    minWidth: 180,
    padding: '8px 10px',
    border: '1px solid #cbd5e1',
    borderRadius: 6,
    fontSize: 13,
  },
  hint: {
    marginTop: 8,
    fontSize: 11,
    color: '#64748b',
  },
}
