import { useScanStore } from '../store/scanStore'

export function StatsPanel() {
  const { stats } = useScanStore()
  const total =
    stats.valid +
    stats.invalid +
    stats.duplicate +
    stats.pending +
    stats.scanned +
    stats.overflow +
    stats.unknown_product

  return (
    <div className="stats">
      <Row label="Всего" value={total} />
      {stats.scanned > 0 && <Row label="Не проверено" value={stats.scanned} variant="warn" />}
      <Row label="Валидных" value={stats.valid} variant="ok" />
      {stats.overflow > 0 && <Row label="Сверх плана" value={stats.overflow} variant="error" />}
      {stats.unknown_product > 0 && (
        <Row label="Без товара" value={stats.unknown_product} variant="warn" />
      )}
      <Row label="Ошибок" value={stats.invalid} variant="error" />
      <Row label="Дублей" value={stats.duplicate} variant="warn" />
      {stats.pending > 0 && <Row label="Проверяется" value={stats.pending} />}
    </div>
  )
}

function Row({ label, value, variant }: {
  label: string
  value: number
  variant?: 'ok' | 'error' | 'warn'
}) {
  const cls = variant ? `stats__value is-${variant}` : 'stats__value'
  return (
    <div className="stats__row">
      <span className="stats__label">{label}</span>
      <span className={cls}>{value}</span>
    </div>
  )
}
