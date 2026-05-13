import { useScanStore, buildProgress } from '../store/scanStore'
import type { CSSProperties } from 'react'

export function ProgressTable() {
  const plan = useScanStore((s) => s.document?.plan)
  const scans = useScanStore((s) => s.scans)
  const overflow = useScanStore((s) => s.stats.overflow)
  const targetProductId = useScanStore((s) => s.targetProductId)
  const setTargetProductId = useScanStore((s) => s.setTargetProductId)
  const progress = buildProgress(plan, scans)

  if (!progress.hasSummary) return null

  const title = progress.hasPlan ? 'Прогресс сборки' : 'По товарам'
  const canPickProduct =
    progress.hasPlan && progress.rows.some((r) => r.product_id)

  return (
    <div style={styles.wrap}>
      <div style={styles.head}>
        <span style={styles.title}>{title}</span>
        {progress.hasPlan ? (
          <span style={styles.totals}>
            В плане: {progress.total.scanned} / {progress.total.expected}
            {progress.total.addedTotal !== progress.total.scanned && (
              <span style={{ marginLeft: 8, color: '#6b7280' }}>
                · кодов: {progress.total.addedTotal}
              </span>
            )}
          </span>
        ) : (
          <span style={styles.totals}>
            Добавлено кодов: {progress.total.addedTotal}
          </span>
        )}
      </div>
      {canPickProduct && (
        <div style={styles.targetBar}>
          <span style={styles.targetLabel}>Сканировать в товар:</span>
          <button
            type="button"
            className="button"
            style={{
              ...styles.targetBtn,
              ...(!targetProductId
                ? { background: '#eff6ff', borderColor: '#93c5fd', color: '#1e40af' }
                : {}),
            }}
            onClick={() => setTargetProductId(null)}
          >
            Авто (по GTIN / плану)
          </button>
        </div>
      )}
      <div style={styles.list}>
        {progress.rows.map((item) => {
          const overLine = item.expected > 0 && item.addedTotal > item.expected
          const pct =
            item.expected > 0
              ? Math.min(100, Math.round((item.addedTotal / item.expected) * 100))
              : item.addedTotal > 0
                ? 100
                : 0
          const ratio = item.expected > 0 ? item.addedTotal / item.expected : item.addedTotal > 0 ? 1 : 0
          const color =
            item.addedTotal === 0 && !item.pendingCount
              ? '#9ca3af'
              : ratio >= 1 && item.expected > 0
                ? '#16a34a'
                : item.addedTotal > 0 || item.pendingCount
                  ? '#f59e0b'
                  : '#9ca3af'

          const countLabel = progress.hasPlan
            ? `Добавлено ${item.addedTotal}${item.expected > 0 ? ` из ${item.expected}` : ''}`
            : `Добавлено: ${item.addedTotal}`

          const isTarget = Boolean(item.product_id && targetProductId === item.product_id)
          const rowStyle: CSSProperties = {
            ...styles.row,
            cursor: item.product_id ? 'pointer' : 'default',
            outline: isTarget ? '2px solid #2563eb' : undefined,
            outlineOffset: 2,
            borderRadius: 6,
            padding: item.product_id ? '2px 4px' : undefined,
            margin: item.product_id ? '-2px -4px' : undefined,
          }

          return (
            <div
              key={item.product_id ? `${item.product_id}:${item.gtin}` : item.gtin}
              style={rowStyle}
              role={item.product_id ? 'button' : undefined}
              tabIndex={item.product_id ? 0 : undefined}
              onClick={() => item.product_id && setTargetProductId(item.product_id)}
              onKeyDown={(e) => {
                if (!item.product_id) return
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setTargetProductId(item.product_id)
                }
              }}
            >
              <div style={styles.rowHead}>
                <span style={styles.name}>{item.product_name || item.gtin}</span>
                <span style={{ ...styles.count, color }} title={item.gtin}>
                  {countLabel}
                  {item.pendingCount ? (
                    <span style={{ color: '#6b7280', fontWeight: 400, marginLeft: 6 }}>
                      · на проверке {item.pendingCount}
                    </span>
                  ) : null}
                </span>
              </div>
              <div style={styles.barWrap}>
                <div style={{ ...styles.bar, width: `${pct}%`, background: color }} />
              </div>
              {overLine && (
                <div style={styles.rowOver}>
                  Вкл. сверх плана: {item.addedTotal - item.expected}
                </div>
              )}
            </div>
          )
        })}
      </div>
      {overflow > 0 && progress.hasPlan && (
        <div style={styles.overflowNote}>
          Всего сверх плана: {overflow}{' '}
          <span style={{ color: '#9ca3af' }}>— уйдут в отгрузку вместе с валидными</span>
        </div>
      )}
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  wrap: {
    background: '#fff',
    border: '1px solid #e5e7eb',
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
  },
  head: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    marginBottom: 10,
    flexWrap: 'wrap',
    gap: 8,
  },
  title: {
    fontSize: 13,
    fontWeight: 600,
    color: '#1f2937',
  },
  totals: {
    fontSize: 13,
    color: '#6b7280',
    fontVariantNumeric: 'tabular-nums',
  },
  targetBar: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
    paddingBottom: 10,
    borderBottom: '1px solid #f1f5f9',
  },
  targetLabel: {
    fontSize: 12,
    color: '#64748b',
  },
  targetBtn: {
    fontSize: 12,
    padding: '4px 10px',
  },
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  row: {},
  rowHead: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    fontSize: 12,
    marginBottom: 4,
    gap: 8,
  },
  name: {
    color: '#1f2937',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    minWidth: 0,
    flex: 1,
  },
  count: {
    fontWeight: 500,
    fontVariantNumeric: 'tabular-nums',
    whiteSpace: 'nowrap',
    flexShrink: 0,
  },
  barWrap: {
    height: 6,
    background: '#f3f4f6',
    borderRadius: 3,
    overflow: 'hidden',
  },
  bar: {
    height: '100%',
    transition: 'width 0.2s ease, background 0.2s ease',
  },
  rowOver: {
    fontSize: 11,
    color: '#b45309',
    marginTop: 4,
  },
  overflowNote: {
    marginTop: 10,
    paddingTop: 10,
    borderTop: '1px dashed #fecaca',
    fontSize: 12,
    color: '#b91c1c',
  },
}
