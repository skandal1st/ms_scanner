import { Fragment, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useColumnWidths } from '../hooks/useColumnWidths'

export interface ColumnDef<T> {
  key: string
  header: ReactNode
  /** Ширина по умолчанию, px. */
  width: number
  minWidth?: number
  render: (row: T) => ReactNode
  className?: string
}

interface ResizableTableProps<T> {
  columns: ColumnDef<T>[]
  rows: T[]
  rowKey: (row: T) => string
  /** Ключ localStorage для сохранения ширин столбцов. */
  storageKey: string
  emptyText?: string
  rowClassName?: (row: T) => string | undefined
  /** Если задан — строка кликабельна и разворачивает блок с этим содержимым. */
  renderExpanded?: (row: T) => ReactNode
}

/**
 * Таблица с изменяемой шириной столбцов (drag за границу заголовка).
 * Ширины сохраняются в localStorage по storageKey. table-layout: fixed —
 * столбцы строго следуют заданным ширинам, при переполнении — горизонтальный скролл.
 */
export function ResizableTable<T>({
  columns,
  rows,
  rowKey,
  storageKey,
  emptyText = 'Нет данных',
  rowClassName,
  renderExpanded,
}: ResizableTableProps<T>) {
  const defaults = useMemo(
    () => Object.fromEntries(columns.map((c) => [c.key, c.width])),
    [columns],
  )
  const { widths, startResize } = useColumnWidths(storageKey, defaults)
  const [expanded, setExpanded] = useState<string | null>(null)

  const totalWidth = columns.reduce((sum, c) => sum + (widths[c.key] ?? c.width), 0)

  return (
    <table className="ui-table resizable-table" style={{ width: totalWidth }}>
      <colgroup>
        {columns.map((c) => (
          <col key={c.key} style={{ width: widths[c.key] ?? c.width }} />
        ))}
      </colgroup>
      <thead>
        <tr>
          {columns.map((c, i) => (
            <th key={c.key} className={c.className}>
              <span className="rt-th-label">{c.header}</span>
              {i < columns.length - 1 && (
                <span
                  className="col-resize-handle"
                  onMouseDown={startResize(c.key, c.minWidth ?? 60)}
                  role="separator"
                  aria-orientation="vertical"
                />
              )}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr>
            <td colSpan={columns.length} className="rt-empty">
              {emptyText}
            </td>
          </tr>
        )}
        {rows.map((row) => {
          const key = rowKey(row)
          const isExpanded = expanded === key
          const clickable = !!renderExpanded
          return (
            <Fragment key={key}>
              <tr
                className={[
                  rowClassName?.(row),
                  clickable ? 'rt-row-clickable' : undefined,
                ]
                  .filter(Boolean)
                  .join(' ')}
                onClick={
                  clickable
                    ? () => setExpanded(isExpanded ? null : key)
                    : undefined
                }
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.className} title={cellTitle(c.render(row))}>
                    {c.render(row)}
                  </td>
                ))}
              </tr>
              {isExpanded && renderExpanded && (
                <tr className="rt-expanded">
                  <td colSpan={columns.length}>{renderExpanded(row)}</td>
                </tr>
              )}
            </Fragment>
          )
        })}
      </tbody>
    </table>
  )
}

/** Подсказка-tooltip для усечённого текста ячейки (только для строковых значений). */
function cellTitle(value: ReactNode): string | undefined {
  return typeof value === 'string' || typeof value === 'number'
    ? String(value)
    : undefined
}
