import type { Scan } from '../api/client'

export function scanPackageType(scan: Scan): 'UNIT' | 'GROUP' | 'BOX' {
  if (scan.package_type) return scan.package_type
  if (scan.is_box) return 'BOX'
  if (!scan.is_barcode && ((scan.box_quantity || 0) > 1 || scan.child_codes?.length)) return 'GROUP'
  return 'UNIT'
}

export function scanPackageLabel(scan: Scan): string {
  if (scan.is_barcode) return `Штрихкод · ${scan.box_quantity || 1} шт.`
  const type = scanPackageType(scan)
  if (type === 'UNIT') return 'Отдельная марка · 1 шт.'
  const quantity = scan.box_quantity || scan.child_codes?.length || '?'
  return `${type === 'BOX' ? 'Короб' : 'Блок'} · ${quantity} шт. · ${scan.is_box || scan.keep_aggregate ? 'целиком' : scan.child_codes?.length ? 'вложенные марки' : 'состав не получен'}`
}
