import { useEffect, useState } from 'react'
import {
  useScanStore,
  buildProgress,
  normalizeGtinKey,
  selectionMatchesRow,
} from '../store/scanStore'
import type { OffPlanRow } from '../store/scanStore'
import { scansApi } from '../api/client'
import { useResizableHeight } from '../hooks/useResizableHeight'
import { Icon } from './Icon'
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
  // Высота списка плана — тянется мышью за разделитель под ним; остаток отдаётся
  // таблице кодов (она flex:1 в .acc-right). reserveBottom бережёт минимум под коды.
  const { height: listHeight, startResize } = useResizableHeight(
    'progress_list_height',
    280,
    { min: 100, max: 900, reserveBottom: 220 },
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
          <Icon
            name="chevron"
            size={15}
            style={{
              ...styles.chevron,
              transform: collapsed ? 'rotate(-90deg)' : 'none',
              transition: 'transform 0.15s ease',
            }}
          />
          {title}
        </span>
        {progress.hasPlan ? (
          <span style={styles.totals}>
            В плане: {progress.total.scanned} / {progress.total.expected}
            {progress.total.addedTotal !== progress.total.scanned && (
              <span style={{ marginLeft: 8, color: 'var(--ms-text-subtle)' }}>
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
              background: overallOver
                ? 'var(--st-err-fg)'
                : overallPct >= 100
                  ? 'var(--st-ok-fg)'
                  : 'var(--st-warn-fg)',
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
                ? { background: 'var(--brand-weak)', borderColor: 'var(--brand)', color: 'var(--brand-strong)' }
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
      {!collapsed && <div style={{ ...styles.list, maxHeight: listHeight }}>
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
            ? 'var(--st-err-fg)' // перевыполнено — красная полоска
            : item.addedTotal === 0 && !item.pendingCount
              ? 'var(--ms-text-subtle)'
              : ratio >= 1 && item.expected > 0
                ? 'var(--st-ok-fg)'
                : item.addedTotal > 0 || item.pendingCount
                  ? 'var(--st-warn-fg)'
                  : 'var(--ms-text-subtle)'

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
            outline: isTarget || isSelected ? '2px solid var(--brand)' : undefined,
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
                    <span style={{ color: 'var(--ms-text-subtle)', fontWeight: 400, marginLeft: 6 }}>
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
      {!collapsed && (
        <div
          className="acc-hsplit"
          onMouseDown={startResize}
          role="separator"
          aria-orientation="horizontal"
          title="Потяните, чтобы изменить высоту панели прогресса"
        />
      )}
      {!collapsed && progress.offPlanRows.length > 0 && (
        <div style={styles.offPlanWrap}>
          <div style={styles.offPlanHead}>
            <Icon name="warning" size={14} /> Не входят в план ({progress.offPlanRows.length}) — отсканированы ошибочно
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
                {deletingOff === row.gtin ? 'Удаляю…' : <><Icon name="close" size={13} /> Удалить</>}
              </button>
            </div>
          ))}
        </div>
      )}
      {!collapsed && overflow > 0 && progress.hasPlan && (
        <div style={styles.overflowNote}>
          Всего сверх плана: {overflow}{' '}
          <span style={{ color: 'var(--ms-text-subtle)' }}>— уйдут в отгрузку вместе с валидными</span>
        </div>
      )}
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  wrap: {
    background: 'var(--ms-bg)',
    border: '1px solid var(--ms-border-light)',
    borderRadius: 'var(--r-md)',
    padding: 14,
    marginBottom: 12,
    boxShadow: 'var(--shadow-1)',
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
    color: 'var(--ms-text)',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
  },
  chevron: {
    color: 'var(--ms-text-subtle)',
    flexShrink: 0,
  },
  totals: {
    fontSize: 13,
    color: 'var(--ms-text-muted)',
    fontVariantNumeric: 'tabular-nums',
  },
  targetBar: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
    paddingBottom: 10,
    borderBottom: '1px solid var(--ms-border-light)',
  },
  targetLabel: {
    fontSize: 12,
    color: 'var(--ms-text-muted)',
  },
  targetBtn: {
    fontSize: 12,
    padding: '4px 10px',
  },
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
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
    color: 'var(--ms-text)',
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
    color: 'var(--st-info-fg)',
    background: 'var(--st-info-bg)',
    border: '1px solid var(--st-info-bd)',
    borderRadius: 'var(--r-sm)',
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
    background: 'var(--surface-2)',
    borderRadius: 'var(--r-pill)',
    overflow: 'hidden',
  },
  bar: {
    height: '100%',
    borderRadius: 'var(--r-pill)',
    transition: 'width 0.25s ease, background 0.25s ease',
  },
  rowOver: {
    fontSize: 11,
    color: 'var(--st-warn-fg)',
    marginTop: 4,
  },
  overflowNote: {
    marginTop: 10,
    paddingTop: 10,
    borderTop: '1px dashed var(--st-err-bd)',
    fontSize: 12,
    color: 'var(--st-err-fg)',
  },
  offPlanWrap: {
    marginTop: 10,
    padding: 12,
    background: 'var(--st-err-bg)',
    border: '1px solid var(--st-err-bd)',
    borderRadius: 'var(--r-md)',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  offPlanHead: {
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--st-err-fg)',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 5,
  },
  offPlanRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  offPlanName: {
    fontSize: 12,
    color: 'var(--ms-text)',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    minWidth: 0,
    flex: 1,
  },
  offPlanCount: {
    color: 'var(--ms-text-muted)',
    fontVariantNumeric: 'tabular-nums',
  },
  offPlanDel: {
    fontSize: 12,
    padding: '4px 10px',
    color: 'var(--st-err-fg)',
    borderColor: 'var(--st-err-bd)',
    flexShrink: 0,
  },
}
