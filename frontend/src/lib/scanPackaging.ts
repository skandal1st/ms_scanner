import type { Scan } from '../api/client'

export function scanPackageType(scan: Scan): 'UNIT' | 'GROUP' | 'BOX' {
  if (scan.package_type) return scan.package_type
  if (scan.is_box) return 'BOX'
  if (!scan.is_barcode && ((scan.box_quantity || 0) > 1 || scan.child_codes?.length)) return 'GROUP'
  return 'UNIT'
}

export function scanPackageLabel(scan: Scan): string {
  if (scan.status === 'pending' && scan.is_box && scan.box_quantity == null) return 'Короб · проверяется в фоне…'
  if (scan.status === 'pending' && scan.verification?.source === 'packaging_pending') return 'Определяем упаковку…'
  if (scan.is_barcode) return `Штрихкод · ${scan.box_quantity || 1} шт.`
  const type = scanPackageType(scan)
  if (type === 'UNIT') return 'Отдельная марка · 1 шт.'
  const quantity = scan.box_quantity || scan.child_codes?.length || '?'
  return `${type === 'BOX' ? 'Короб' : 'Блок'} · ${quantity} шт. · ${isWholePackage(scan) ? 'целиком' : scan.child_codes?.length ? 'вложенные марки' : 'состав не получен'}`
}

export function isWholePackage(scan: Scan): boolean {
  return scan.is_box ? !scan.box_expanded : Boolean(scan.keep_aggregate)
}

export function shouldUnpack(scan: Scan): boolean {
  return isWholePackage(scan) || !scan.child_codes?.length
}

export function packActionLabel(scan: Scan): string {
  const name = scanPackageType(scan) === 'BOX' ? 'короб' : 'блок'
  return shouldUnpack(scan) ? `Раскрыть ${name} на вложенные марки` : `Сохранить ${name} целиком`
}
