import type { Scan } from '../api/client'

export function verificationLabel(scan: Scan): string {
  if (scan.is_barcode) return 'Штрихкод'
  if (scan.verification?.ms_error) return 'Отклонена МойСкладом'
  if (scan.status === 'invalid') return 'Есть проблема'
  if (scan.verification?.source === 'pending') return 'Проверяется в ЧЗ'
  if (scan.verification?.source === 'ms') return 'Загружена из МС'
  if (scan.verification?.source === 'format') return 'Проверен только формат'
  if (scan.verification?.source === 'cz_contents' && scan.verification.checked_at && ['valid', 'overflow'].includes(scan.status)) return 'Вложенные марки проверены в ЧЗ'
  if (['valid', 'overflow'].includes(scan.status) && scan.verification?.checked_at
      && scan.verification.source.startsWith('cz_')) return 'Проверена в ЧЗ'
  return 'Проверка ЧЗ не подтверждена'
}

export function ownerCheckLabel(scan: Scan, reference?: string | null): string {
  if (!reference) return 'Нет ИНН подписи ЧЗ для сравнения'
  return scan.verification?.owner_reason || (scan.verification?.checked_at
    ? 'ЧЗ не вернул ИНН владельца' : 'Проверка владельца в ЧЗ не выполнена')
}
