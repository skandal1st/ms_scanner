import type { Scan } from '../api/client'
import { effectiveGtinKey, findProgressRowForScan, ownerCheckState } from '../store/scanStore'
import type { PlanProgress, ProgressRow } from '../store/scanStore'

export function isProblemScan(scan: Scan, signatureInn?: string | null, repeatedId?: string | null, planRows?: ProgressRow[]): boolean {
  return scan.id === repeatedId
    || Boolean(scan.status !== 'pending' && planRows?.length && !findProgressRowForScan(scan, planRows))
    || ['invalid', 'duplicate', 'overflow', 'unknown_product', 'used_in_other_doc'].includes(scan.status)
    || Boolean(scan.withdrawn || scan.verification?.ms_error || scan.verification?.child_issues?.length
      || scan.verification?.owner_result === 'mismatch' || scan.verification?.owner_result === 'unknown'
      || ownerCheckState(scan, signatureInn))
}

/** Number the whole current list chronologically, independently of filters and verification updates. */
export function numberedScans(scans: Scan[]) {
  const ordered = [...scans].sort((a, b) =>
    (Date.parse(a.scanned_at) - Date.parse(b.scanned_at)) || a.id.localeCompare(b.id))
  return ordered.map((scan, index) => ({ scan, number: index + 1 })).reverse()
}

export function problemPositionRows(progress: PlanProgress, scans: Scan[], signatureInn?: string | null, repeatedId?: string | null): ProgressRow[] {
  const problemRows = new Set<ProgressRow>()
  const extraRows: ProgressRow[] = []
  const offPlanIds = new Set(progress.offPlanRows.flatMap(row => row.scanIds))
  for (const scan of scans) {
    if (!isProblemScan(scan, signatureInn, repeatedId, progress.hasPlan ? progress.rows : undefined)) continue
    const row = findProgressRowForScan(scan, progress.rows)
    if (row) problemRows.add(row)
    else if (!offPlanIds.has(scan.id)) {
      // Rejected marks do not contribute to progress, but their positions must remain inspectable.
      if (!findProgressRowForScan(scan, extraRows)) extraRows.push({
        gtin: effectiveGtinKey(scan) || scan.code,
        product_id: scan.moysklad_product_id || null,
        product_name: scan.product_name || 'Неизвестный товар',
        expected: 0, scanned: 0, addedTotal: 0,
      })
    }
  }
  return [...progress.rows.filter(row => problemRows.has(row)
    || (progress.hasPlan && row.addedTotal !== row.expected)), ...extraRows]
}
