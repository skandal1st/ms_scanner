import { useState } from 'react'
import { useScanStore } from '../store/scanStore'
import { scansApi, type Scan } from '../api/client'

const STATUS_CONFIG = {
  pending:   { label: 'Проверяется', cls: 'badge--pending' },
  valid:     { label: 'Валиден',     cls: 'badge--ok' },
  invalid:   { label: 'Ошибка',      cls: 'badge--error' },
  duplicate: { label: 'Дубль',       cls: 'badge--warn' },
  overflow:  { label: 'Сверх плана', cls: 'badge--error' },
} as const

export function CodesTable() {
  const { scans, removeScan } = useScanStore()
  const [expanded, setExpanded] = useState<string | null>(null)

  if (scans.length === 0) {
    return <div className="scans-empty">Коды появятся здесь после сканирования</div>
  }

  return (
    <table className="ui-table scans-table">
      <thead>
        <tr>
          <th>Код</th>
          <th>Товар</th>
          <th>Статус</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {scans.map((scan) => (
          <ScanRow
            key={scan.id}
            scan={scan}
            isExpanded={expanded === scan.id}
            onToggle={() => setExpanded(expanded === scan.id ? null : scan.id)}
            onDelete={async () => {
              await scansApi.delete(scan.id)
              removeScan(scan.id)
            }}
          />
        ))}
      </tbody>
    </table>
  )
}

function ScanRow({
  scan,
  isExpanded,
  onToggle,
  onDelete,
}: {
  scan: Scan
  isExpanded: boolean
  onToggle: () => void
  onDelete: () => void
}) {
  const cfg = STATUS_CONFIG[scan.status]

  return (
    <>
      <tr
        className={`scans-row ${isExpanded ? 'is-expanded' : ''}`}
        onClick={onToggle}
      >
        <td className="is-code">
          {scan.code.slice(0, 20)}{scan.code.length > 20 ? '…' : ''}
        </td>
        <td>
          {scan.product_name
            ? scan.product_name
            : <span className="text-muted">—</span>}
        </td>
        <td>
          <span className={`badge ${cfg.cls}`}>{cfg.label}</span>
        </td>
        <td className="is-action">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onDelete() }}
            className="delete-btn"
            title="Удалить"
            aria-label="Удалить"
          >
            ×
          </button>
        </td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={4} className="scans-expanded">
            <div className="scans-expanded__row">
              <span className="scans-expanded__label">Полный код:</span>
              <code>{scan.code}</code>
            </div>
            {scan.gtin && (
              <div className="scans-expanded__row">
                <span className="scans-expanded__label">GTIN:</span>
                <span>{scan.gtin}</span>
              </div>
            )}
            {scan.error_message && (
              <div className="scans-expanded__row">
                <span className="scans-expanded__label">Ошибка:</span>
                <span className="error">{scan.error_message}</span>
              </div>
            )}
            <div className="scans-expanded__row text-muted">
              <span className="scans-expanded__label">Время:</span>
              <span>{new Date(scan.scanned_at).toLocaleTimeString('ru')}</span>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
