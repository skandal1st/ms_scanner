import { useScanStore, buildProgress } from '../store/scanStore'
import type { CSSProperties } from 'react'

export function ProgressTable() {
  // Подписываемся на примитивные срезы стора, а сам прогресс считаем здесь.
  // Если селектор вернёт новый объект на каждом вызове — useSyncExternalStore
  // зациклится с "Maximum update depth exceeded" и выкинет белый экран.
  const plan = useScanStore((s) => s.document?.plan)
  const scans = useScanStore((s) => s.scans)
  const overflow = useScanStore((s) => s.stats.overflow)
  const progress = buildProgress(plan, scans)

  if (!progress.hasPlan) return null

  const items = Object.entries(progress.byGtin).map(([gtin, p]) => ({
    gtin,
    ...p,
  }))

  return (
    <div style={styles.wrap}>
      <div style={styles.head}>
        <span style={styles.title}>Прогресс сборки</span>
        <span style={styles.totals}>
          {progress.total.scanned} / {progress.total.expected}
        </span>
      </div>
      <div style={styles.list}>
        {items.map((item) => {
          const pct = item.expected > 0
            ? Math.min(100, Math.round((item.scanned / item.expected) * 100))
            : 0
          const ratio = item.expected > 0 ? item.scanned / item.expected : 0
          const color =
            item.scanned === 0
              ? '#9ca3af' // серый
              : ratio >= 1
                ? '#16a34a' // зелёный
                : '#f59e0b' // жёлтый

          return (
            <div key={item.gtin} style={styles.row}>
              <div style={styles.rowHead}>
                <span style={styles.name}>{item.product_name || item.gtin}</span>
                <span style={{ ...styles.count, color }}>
                  {item.scanned} / {item.expected}
                </span>
              </div>
              <div style={styles.barWrap}>
                <div style={{ ...styles.bar, width: `${pct}%`, background: color }} />
              </div>
            </div>
          )
        })}
      </div>
      {overflow > 0 && (
        <div style={styles.overflowNote}>
          Сверх плана: {overflow}{' '}
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
  },
  name: {
    color: '#1f2937',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    paddingRight: 8,
  },
  count: {
    fontWeight: 500,
    fontVariantNumeric: 'tabular-nums',
    whiteSpace: 'nowrap',
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
  overflowNote: {
    marginTop: 10,
    paddingTop: 10,
    borderTop: '1px dashed #fecaca',
    fontSize: 12,
    color: '#b91c1c',
  },
}
