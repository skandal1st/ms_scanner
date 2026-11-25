import { useScanStore } from '../store/scanStore'

export function StatsPanel() {
  const { stats } = useScanStore()
  const approvedApplied = stats.ms_approved - stats.approved_withdrawn - stats.approved_flagged
  const total =
    stats.valid +
    stats.invalid +
    stats.ms_approved +
    stats.duplicate +
    stats.pending +
    stats.scanned +
    stats.overflow +
    stats.unknown_product +
    stats.used_in_other_doc

  return (
    <div className="stats">
      <Row label="Всего" value={total} />
      {stats.scanned > 0 && <Row label="Ждут проверки" value={stats.scanned} variant="warn" />}
      {stats.valid > 0 && <Row label="Проверено" value={stats.valid} variant="ok" />}
      {approvedApplied > 0 && <Row label="Включено до ввода в оборот" value={approvedApplied} variant="warn" />}
      {stats.approved_withdrawn > 0 && <Row label="Включено после выбытия" value={stats.approved_withdrawn} variant="warn" />}
      {stats.approved_flagged > 0 && <Row label="Включено с признаком выбытия или блокировки" value={stats.approved_flagged} variant="warn" />}
      {stats.overflow > 0 && <Row label="Сверх плана" value={stats.overflow} variant="error" />}
      {stats.unknown_product > 0 && (
        <Row label="Без товара" value={stats.unknown_product} variant="warn" />
      )}
      {stats.invalid > 0 && <Row label="Ошибок" value={stats.invalid} variant="error" />}
      {stats.used_in_other_doc > 0 && <Row label="В другом документе" value={stats.used_in_other_doc} variant="error" />}
      {stats.duplicate > 0 && <Row label="Дублей" value={stats.duplicate} variant="warn" />}
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
