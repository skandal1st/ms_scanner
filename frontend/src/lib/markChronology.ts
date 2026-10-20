import type { CodeSearchHit, UpdSearchHit, MarkHistoryEvent } from '../api/client'

export interface ChronologyEntry {
  id: string
  source: 'Скандата' | 'ЭДО' | 'Честный Знак'
  at: string | null
  dateLabel: string
  title: string
  detail: string
  documentId?: string | null
  recordedAt?: string | null
}

const kinds: Record<string, string> = { supply: 'Приёмка', demand: 'Отгрузка', loss: 'Списание', salesreturn: 'Возврат' }
const docStates: Record<string, string> = { draft: 'Черновик', processing: 'Отправляется', accepted: 'Документ завершён' }
const scanStates: Record<string, string> = { valid: 'Марка проверена', invalid: 'Ошибка марки', scanned: 'Марка не проверена',
  pending: 'Марка проверяется', overflow: 'Марка сверх плана', unknown_product: 'Товар марки не сопоставлен',
  used_in_other_doc: 'Марка используется в другом документе', duplicate: 'Повторный код' }

function updDate(value: string | null) {
  const match = value?.match(/^(\d{2})\.(\d{2})\.(\d{4})$/)
  if (!match) return null
  const [, day, month, year] = match
  const parsed = new Date(`${year}-${month}-${day}T00:00:00+03:00`)
  return Number.isFinite(parsed.getTime()) && new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow' }).format(parsed) === value
    ? parsed.toISOString() : null
}

export function markChronology(scans: CodeSearchHit[], upds: UpdSearchHit[], history: MarkHistoryEvent[] = []): ChronologyEntry[] {
  const entries: ChronologyEntry[] = [
    ...scans.map(scan => ({
      id: `scan:${scan.scan_id}`, source: 'Скандата' as const, at: scan.scanned_at,
      dateLabel: 'Время скана', title: `${kinds[scan.document_kind] || scan.document_kind} ${scan.document_name}`,
      detail: [docStates[scan.document_status || ''] || 'Статус документа неизвестен',
        scan.accepted_without_cz ? 'Принято без проверки ЧЗ' : scanStates[scan.status] || 'Статус марки неизвестен', scan.product_name].filter(Boolean).join(' · '),
    })),
    ...upds.map(upd => ({
      id: `upd:${upd.edo_document_id}`, source: 'ЭДО' as const, at: updDate(upd.doc_date),
      dateLabel: 'Дата УПД', title: `${upd.direction || 'Направление не передано'} УПД${upd.number ? ` № ${upd.number}` : ''}`,
      detail: [upd.counterparty_name || 'Контрагент не передан', upd.counterparty_inn ? `ИНН ${upd.counterparty_inn}` : null,
        upd.state_name || 'Статус УПД неизвестен'].filter(Boolean).join(' · '),
    })),
    ...history.map((event, index) => ({
      id: `cz:${index}`, source: 'Честный Знак' as const, at: event.operation_at,
      dateLabel: 'Дата документа ЧЗ', title: event.status_label,
      detail: event.owner_inn ? `Владелец в событии: ИНН ${event.owner_inn}` : 'Владелец в событии не передан',
      documentId: event.document_id, recordedAt: event.recorded_at,
    })),
  ]
  return entries.sort((a, b) => {
    const left = a.at ? Date.parse(a.at) : NaN
    const right = b.at ? Date.parse(b.at) : NaN
    if (!Number.isFinite(left)) return Number.isFinite(right) ? 1 : 0
    if (!Number.isFinite(right)) return -1
    return left - right
  })
}

export function chronologyDate(value: string | null, dateOnly = false) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Дата неизвестна'
  return new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
    ...(dateOnly ? {} : { hour: '2-digit' as const, minute: '2-digit' as const }) }).format(new Date(value))
}
