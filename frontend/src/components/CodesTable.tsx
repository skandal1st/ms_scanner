import { useState, type CSSProperties } from 'react'
import {
  useScanStore,
  effectiveGtinKey,
  selectionMatchesScan,
  ownerCheckState,
} from '../store/scanStore'
import { scansApi, type Scan } from '../api/client'
import { Icon } from './Icon'

const STATUS_CONFIG = {
  pending:           { label: 'Проверяется',       cls: 'badge--pending' },
  valid:             { label: 'Валиден',           cls: 'badge--ok' },
  invalid:           { label: 'Ошибка',            cls: 'badge--error' },
  duplicate:         { label: 'Дубль',             cls: 'badge--warn' },
  overflow:          { label: 'Сверх плана',       cls: 'badge--error' },
  unknown_product:   { label: 'Нет товара',        cls: 'badge--warn' },
  used_in_other_doc: { label: 'В другом документе', cls: 'badge--warn' },
} as const

export function CodesTable({ signatureInn }: { signatureInn?: string | null }) {
  const {
    scans,
    removeScan,
    flashScanId,
    selection,
    setSelection,
    setTargetProductId,
    togglePositionSelection,
  } = useScanStore()
  const [expanded, setExpanded] = useState<string | null>(null)

  if (scans.length === 0) {
    return <div className="scans-empty">Коды появятся здесь после сканирования</div>
  }

  // Фокус на выбранной позиции: пока активно выделение, показываем только её марки.
  const visibleScans = selection
    ? scans.filter((s) => selectionMatchesScan(selection, s))
    : scans
  const hiddenCount = scans.length - visibleScans.length

  const clearSelection = () => {
    setSelection(null)
    setTargetProductId(null)
  }

  return (
    <>
      {selection && (
        <div style={styles.filterBar}>
          <span style={styles.filterText}>
            Показаны марки выбранной позиции
            {hiddenCount > 0 ? ` · скрыто ${hiddenCount}` : ''}
          </span>
          <button
            type="button"
            className="button"
            style={styles.filterBtn}
            onClick={clearSelection}
          >
            Показать все марки ({scans.length})
          </button>
        </div>
      )}
      {visibleScans.length === 0 ? (
        <div className="scans-empty">В этой позиции пока нет отсканированных марок</div>
      ) : (
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
            {visibleScans.map((scan) => (
              <ScanRow
                key={scan.id}
                scan={scan}
                signatureInn={signatureInn}
                isExpanded={expanded === scan.id}
                isFlash={flashScanId === scan.id}
                isSelected={Boolean(selection && selectionMatchesScan(selection, scan))}
                onToggle={() => {
                  setExpanded(expanded === scan.id ? null : scan.id)
                  // Выделение задаём кликом по марке только когда его ещё нет. Если
                  // позиция уже выбрана — клик лишь разворачивает детали, фильтр
                  // держится (выделение не снимаем).
                  if (!selection) {
                    togglePositionSelection({
                      productId: scan.moysklad_product_id ?? null,
                      gtinKey: effectiveGtinKey(scan),
                    })
                  }
                }}
                onDelete={async () => {
                  await scansApi.delete(scan.id)
                  removeScan(scan.id)
                }}
              />
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

const styles: Record<string, CSSProperties> = {
  filterBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    flexWrap: 'wrap',
    padding: '6px 10px',
    marginBottom: 8,
    background: 'var(--brand-weak)',
    border: '1px solid var(--st-info-bd)',
    borderRadius: 'var(--r-md)',
  },
  filterText: {
    fontSize: 12,
    color: 'var(--brand-strong)',
    fontVariantNumeric: 'tabular-nums',
  },
  filterBtn: {
    fontSize: 12,
    padding: '4px 10px',
    flexShrink: 0,
  },
}

function ScanRow({
  scan,
  signatureInn,
  isExpanded,
  isFlash,
  isSelected,
  onToggle,
  onDelete,
}: {
  scan: Scan
  signatureInn?: string | null
  isExpanded: boolean
  isFlash: boolean
  isSelected: boolean
  onToggle: () => void
  onDelete: () => void
}) {
  const cfg = STATUS_CONFIG[scan.status]
  // Сверка владельца марки с владельцем подписи (только отгрузка). Подсветка, не блокировка.
  const owner = ownerCheckState(scan, signatureInn)
  const ownerCls =
    owner === 'mismatch' ? 'is-owner-mismatch' : owner === 'unknown' ? 'is-owner-unknown' : ''
  // Марка выведена из оборота / заблокирована (ЧЗ). Подсветка + предупреждение, не блокирует.
  const withdrawnCls = scan.withdrawn ? 'is-withdrawn' : ''
  // Агрегат (блок/короб): развёрнут в единицы — box_quantity/child_codes без is_box.
  // Штрихкод немаркированного товара (is_barcode) — не агрегат: box_quantity = кол-во.
  const childCount = scan.child_codes?.length ?? 0
  const isAggregate =
    !scan.is_box && !scan.is_barcode && (childCount > 0 || scan.box_quantity != null)

  return (
    <>
      <tr
        className={`scans-row ${isExpanded ? 'is-expanded' : ''} ${isFlash ? 'is-flash' : ''} ${isSelected ? 'is-selected' : ''} ${ownerCls} ${withdrawnCls}`}
        onClick={onToggle}
      >
        <td className="is-code">
          {(scan.is_box || isAggregate) && (
            <Icon name="box" size={13} style={{ verticalAlign: '-2px', marginRight: 4, color: 'var(--ms-text-muted)' }} />
          )}
          {scan.is_barcode && (
            <Icon name="scan" size={13} style={{ verticalAlign: '-2px', marginRight: 4, color: 'var(--ms-text-muted)' }} />
          )}
          {scan.code.slice(0, 20)}{scan.code.length > 20 ? '…' : ''}
        </td>
        <td>
          {scan.product_name ? (
            scan.product_name
          ) : (
            <span className="text-muted">—</span>
          )}
          {scan.is_box ? (
            <div style={{ fontSize: 10, marginTop: 2, color: 'var(--brand)' }}>
              Короб · {scan.box_quantity ?? '?'} шт.
            </div>
          ) : null}
          {isAggregate ? (
            <div style={{ fontSize: 10, marginTop: 2, color: 'var(--brand)' }}>
              Упаковка · {scan.box_quantity ?? childCount} шт.
            </div>
          ) : null}
          {scan.is_barcode ? (
            <div style={{ fontSize: 10, marginTop: 2, color: 'var(--brand)' }}>
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
          {scan.withdrawn && (
            <span
              className="badge badge--error"
              style={{ marginLeft: 4 }}
              title={
                scan.withdraw_reason
                  ? `Выведена из оборота (${scan.withdraw_reason})`
                  : 'Марка выведена из оборота / заблокирована'
              }
            >
              Выведена из оборота
            </span>
          )}
          {owner === 'mismatch' && (
            <span
              className="badge badge--warn"
              style={{ marginLeft: 4 }}
              title="ИНН владельца марки не совпадает с владельцем подписи"
            >
              Чужой владелец
            </span>
          )}
          {owner === 'unknown' && (
            <span
              className="badge badge--pending"
              style={{ marginLeft: 4 }}
              title="Не удалось проверить владельца марки в Честном Знаке"
            >
              Владелец не проверен
            </span>
          )}
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
            {(scan.owner_name || scan.owner_inn) && (
              <div className="scans-expanded__row">
                <span className="scans-expanded__label">Владелец:</span>
                <span>
                  {scan.owner_name}
                  {scan.owner_inn ? ` (ИНН ${scan.owner_inn})` : ''}
                </span>
              </div>
            )}
            {scan.withdrawn && (
              <div className="scans-expanded__row">
                <span className="scans-expanded__label">Выведена из оборота:</span>
                <span className="error">
                  да{scan.withdraw_reason ? ` · ${scan.withdraw_reason}` : ''}
                </span>
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
