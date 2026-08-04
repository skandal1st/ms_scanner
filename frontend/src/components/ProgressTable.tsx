import { useEffect, useState } from 'react'
import {
  useScanStore,
  buildProgress,
  normalizeGtinKey,
  selectionMatchesRow,
} from '../store/scanStore'
import type { OffPlanRow } from '../store/scanStore'
import { scansApi } from '../api/client'
import type { CSSProperties } from 'react'

const COLLAPSE_KEY = 'progress_collapsed'

export function ProgressTable() {
  const plan = useScanStore((s) => s.document?.plan)
  const scans = useScanStore((s) => s.scans)
  const overflow = useScanStore((s) => s.stats.overflow)
  const targetProductId = useScanStore((s) => s.targetProductId)
  const setTargetProductId = useScanStore((s) => s.setTargetProductId)
  const selection = useScanStore((s) => s.selection)
  const setSelection = useScanStore((s) => s.setSelection)
  const togglePositionSelection = useScanStore((s) => s.togglePositionSelection)
  const documentId = useScanStore((s) => s.document?.id)
  const removeScan = useScanStore((s) => s.removeScan)
  const progress = buildProgress(plan, scans)
  const [deletingOff, setDeletingOff] = useState<string | null>(null)

  const handleDeleteOffPlan = async (row: OffPlanRow) => {
    if (!documentId) return
    const ok = window.confirm(
      `Удалить позицию «${row.product_name}» (${row.scanIds.length} код(ов))? ` +
        `Она не входит в план. Действие необратимо.`,
    )
    if (!ok) return
    setDeletingOff(row.gtin)
    try {
      await scansApi.deleteBulk(documentId, row.scanIds)
      for (const id of row.scanIds) removeScan(id)
    } finally {
      setDeletingOff(null)
    }
  }
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(COLLAPSE_KEY) === '1',
  )

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
  }, [collapsed])

  if (!progress.hasSummary) return null

  const title = progress.hasPlan ? 'Прогресс сборки' : 'По товарам'
  const canPickProduct =
    progress.hasPlan && progress.rows.some((r) => r.product_id)
  const overallPct =
    progress.hasPlan && progress.total.expected > 0
      ? Math.min(100, Math.round((progress.total.scanned / progress.total.expected) * 100))
      : 0
  const overallOver =
    progress.hasPlan &&
    progress.total.expected > 0 &&
    progress.total.addedTotal > progress.total.expected

  return (
    <div style={styles.wrap}>
      <div
        style={{ ...styles.head, cursor: 'pointer', marginBottom: collapsed ? 6 : 10 }}
        onClick={() => setCollapsed((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setCollapsed((v) => !v)
          }
        }}
        aria-expanded={!collapsed}
      >
        <span style={styles.title}>
          <span style={styles.chevron}>{collapsed ? '▸' : '▾'}</span>
          {title}
        </span>
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
      {collapsed && progress.hasPlan && (
        <div style={styles.barWrap}>
          <div
            style={{
              ...styles.bar,
              width: `${overallPct}%`,
              background: overallOver ? '#dc2626' : overallPct >= 100 ? '#16a34a' : '#f59e0b',
            }}
          />
        </div>
      )}
      {!collapsed && canPickProduct && (
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
            onClick={() => {
              setTargetProductId(null)
              setSelection(null)
            }}
          >
            Авто (по GTIN / плану)
          </button>
        </div>
      )}
      {!collapsed && <div style={styles.list}>
        {progress.rows.map((item) => {
          const overLine = item.expected > 0 && item.addedTotal > item.expected
          const pct =
            item.expected > 0
              ? Math.min(100, Math.round((item.addedTotal / item.expected) * 100))
              : item.addedTotal > 0
                ? 100
                : 0
          const ratio = item.expected > 0 ? item.addedTotal / item.expected : item.addedTotal > 0 ? 1 : 0
          const color = overLine
            ? '#dc2626' // перевыполнено — красная полоска
            : item.addedTotal === 0 && !item.pendingCount
              ? '#9ca3af'
              : ratio >= 1 && item.expected > 0
                ? '#16a34a'
                : item.addedTotal > 0 || item.pendingCount
                  ? '#f59e0b'
                  : '#9ca3af'

          const countLabel = progress.hasPlan
            ? `Добавлено ${item.addedTotal}${item.expected > 0 ? ` из ${item.expected}` : ''}`
            : `Добавлено: ${item.addedTotal}`

          const gtinKey = normalizeGtinKey(item.gtin)
          const selectable = Boolean(item.product_id || gtinKey)
          const isTarget = Boolean(item.product_id && targetProductId === item.product_id)
          const isSelected = Boolean(selection && selectionMatchesRow(selection, item))
          const select = () =>
            togglePositionSelection({ productId: item.product_id, gtinKey })
          const rowStyle: CSSProperties = {
            ...styles.row,
            cursor: selectable ? 'pointer' : 'default',
            outline: isTarget || isSelected ? '2px solid #2563eb' : undefined,
            outlineOffset: 2,
            borderRadius: 6,
            padding: selectable ? '2px 4px' : undefined,
            margin: selectable ? '-2px -4px' : undefined,
          }

          return (
            <div
              key={item.product_id ? `${item.product_id}:${item.gtin}` : item.gtin}
              style={rowStyle}
              role={selectable ? 'button' : undefined}
              tabIndex={selectable ? 0 : undefined}
              onClick={() => selectable && select()}
              onKeyDown={(e) => {
                if (!selectable) return
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  select()
                }
              }}
            >
              <div style={styles.rowHead}>
                <span style={styles.name}>
                  {item.product_name || item.gtin}
                  {item.unmarked && (
                    <span style={styles.barcodeTag} title="Немаркированный товар — сканируйте штрихкод">
                      штрихкод
                    </span>
                  )}
                </span>
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
      </div>}
      {!collapsed && progress.offPlanRows.length > 0 && (
        <div style={styles.offPlanWrap}>
          <div style={styles.offPlanHead}>
            ⚠ Не входят в план ({progress.offPlanRows.length}) — отсканированы ошибочно
          </div>
          {progress.offPlanRows.map((row) => (
            <div key={row.gtin} style={styles.offPlanRow}>
              <span style={styles.offPlanName} title={row.gtin}>
                {row.product_name}
                <span style={styles.offPlanCount}>
                  {' · '}
                  {row.addedTotal} шт.
                  {row.pendingCount ? ` (+${row.pendingCount} на проверке)` : ''}
                </span>
              </span>
              <button
                type="button"
                className="button"
                style={styles.offPlanDel}
                onClick={() => void handleDeleteOffPlan(row)}
                disabled={deletingOff === row.gtin}
              >
                {deletingOff === row.gtin ? 'Удаляю…' : '✕ Удалить'}
              </button>
            </div>
          ))}
        </div>
      )}
      {!collapsed && overflow > 0 && progress.hasPlan && (
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
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
  },
  chevron: {
    color: '#6b7280',
    fontSize: 11,
    userSelect: 'none',
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
    // Большая отгрузка = много позиций в плане: список не должен вытеснять таблицу
    // кодов и упираться в низ экрана без прокрутки. Ограничиваем высоту и скроллим
    // сам список позиций.
    maxHeight: '38vh',
    overflowY: 'auto',
    // Компенсируем отрицательные поля/outline выделенных строк, чтобы их не
    // подрезал скролл-контейнер и не появлялась горизонтальная прокрутка.
    padding: '4px 6px',
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
  barcodeTag: {
    display: 'inline-block',
    marginLeft: 6,
    padding: '0 6px',
    fontSize: 10,
    fontWeight: 600,
    lineHeight: '16px',
    color: '#3730a3',
    background: '#eef2ff',
    border: '1px solid #c7d2fe',
    borderRadius: 4,
    verticalAlign: 'middle',
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
  offPlanWrap: {
    marginTop: 10,
    padding: 10,
    background: '#fef2f2',
    border: '1px solid #fecaca',
    borderRadius: 6,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  offPlanHead: {
    fontSize: 12,
    fontWeight: 600,
    color: '#b91c1c',
  },
  offPlanRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  offPlanName: {
    fontSize: 12,
    color: '#1f2937',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    minWidth: 0,
    flex: 1,
  },
  offPlanCount: {
    color: '#6b7280',
    fontVariantNumeric: 'tabular-nums',
  },
  offPlanDel: {
    fontSize: 12,
    padding: '4px 10px',
    color: '#b91c1c',
    borderColor: '#fca5a5',
    flexShrink: 0,
  },
}
