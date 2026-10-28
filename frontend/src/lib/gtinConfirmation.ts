export interface GtinConflict { name: string; gtin: string; existing: string[] }
let handler: ((conflicts: GtinConflict[]) => Promise<boolean>) | null = null
export function setGtinConfirmationHandler(value: typeof handler) { handler = value }
export function canonicalGtin(value: string) {
  return /^\d{8}$|^\d{12,14}$/.test(value) ? value.padStart(14, '0') : null
}
export async function confirmGtinConflicts(conflicts: GtinConflict[]) {
  if (conflicts.length && (!handler || !await handler(conflicts))) {
    throw Object.assign(new Error('Запись GTIN отменена'), {
      response: { data: { detail: 'Запись GTIN отменена. Карточка товара не изменена.' } },
    })
  }
}
