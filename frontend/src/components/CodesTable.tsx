import { useState } from 'react'
import {
  useScanStore,
  effectiveGtinKey,
  selectionMatchesScan,
} from '../store/scanStore'
import { scansApi, type Scan } from '../api/client'

const STATUS_CONFIG = {
  pending:           { label: 'Проверяется',       cls: 'badge--pending' },
  valid:             { label: 'Валиден',           cls: 'badge--ok' },
  invalid:           { label: 'Ошибка',            cls: 'badge--error' },
  duplicate:         { label: 'Дубль',             cls: 'badge--warn' },
  overflow:          { label: 'Сверх плана',       cls: 'badge--error' },
  unknown_product:   { label: 'Нет товара',        cls: 'badge--warn' },
  used_in_other_doc: { label: 'В другом документе', cls: 'badge--warn' },
} as const

export function CodesTable() {
  const { scans, removeScan, flashScanId, selection, togglePositionSelection } = useScanStore()
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
            isFlash={flashScanId === scan.id}
            isSelected={Boolean(selection && selectionMatchesScan(selection, scan))}
            onToggle={() => {
              setExpanded(expanded === scan.id ? null : scan.id)
              togglePositionSelection({
                productId: scan.moysklad_product_id ?? null,
                gtinKey: effectiveGtinKey(scan),
              })
            }}
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
  isFlash,
  isSelected,
  onToggle,
  onDelete,
}: {
  scan: Scan
  isExpanded: boolean
  isFlash: boolean
  isSelected: boolean
  onToggle: () => void
  onDelete: () => void
}) {
  const cfg = STATUS_CONFIG[scan.status]
  // Агрегат (блок/короб): развёрнут в единицы — box_quantity/child_codes без is_box.
  // Штрихкод немаркированного товара (is_barcode) — не агрегат: box_quantity = кол-во.
  const childCount = scan.child_codes?.length ?? 0
  const isAggregate =
    !scan.is_box && !scan.is_barcode && (childCount > 0 || scan.box_quantity != null)

  return (
    <>
      <tr
        className={`scans-row ${isExpanded ? 'is-expanded' : ''} ${isFlash ? 'is-flash' : ''} ${isSelected ? 'is-selected' : ''}`}
        onClick={onToggle}
      >
        <td className="is-code">
          {scan.is_box || isAggregate ? '📦 ' : ''}
          {scan.is_barcode ? '🏷️ ' : ''}
          {scan.code.slice(0, 20)}{scan.code.length > 20 ? '…' : ''}
        </td>
        <td>
          {scan.product_name ? (
            scan.product_name
          ) : (
            <span className="text-muted">—</span>
          )}
          {scan.is_box ? (
            <div style={{ fontSize: 10, marginTop: 2, color: 'var(--ms-accent, #2563eb)' }}>
              Короб · {scan.box_quantity ?? '?'} шт.
            </div>
          ) : null}
          {isAggregate ? (
            <div style={{ fontSize: 10, marginTop: 2, color: 'var(--ms-accent, #2563eb)' }}>
              Упаковка · {scan.box_quantity ?? childCount} шт.
            </div>
          ) : null}
          {scan.is_barcode ? (
            <div style={{ fontSize: 10, marginTop: 2, color: 'var(--ms-accent, #2563eb)' }}>
              Штрихкод · {scan.box_quantity ?? 1} шт.
            </div>
          ) : null}
          {scan.moysklad_product_id ? (
            <div className="text-muted" style={{ fontSize: 10, marginTop: 2 }}>
              → товар МС {scan.moysklad_product_id.slice(0, 8)}…
            </div>
          ) : null}
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
            {scan.owner_name && (
              <div className="scans-expanded__row">
                <span className="scans-expanded__label">Владелец:</span>
                <span>{scan.owner_name}</span>
              </div>
            )}
            {scan.producer_name && (
              <div className="scans-expanded__row">
                <span className="scans-expanded__label">Производитель:</span>
                <span>{scan.producer_name}</span>
              </div>
            )}
            {childCount > 0 && (
              <div className="scans-expanded__row" style={{ alignItems: 'flex-start' }}>
                <span className="scans-expanded__label">Коды внутри ({childCount}):</span>
                <div style={{ maxHeight: 160, overflowY: 'auto', fontSize: 11, fontFamily: 'monospace' }}>
                  {scan.child_codes!.map((cc, i) => (
                    <div key={i}>{cc}</div>
                  ))}
                </div>
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
