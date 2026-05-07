import { useScanStore } from '../store/scanStore'

export function StatsPanel() {
  const { stats } = useScanStore()
  const total = stats.valid + stats.invalid + stats.duplicate + stats.pending

  return (
    <div className="stats">
      <Row label="Всего" value={total} />
      <Row label="Валидных" value={stats.valid} variant="ok" />
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
