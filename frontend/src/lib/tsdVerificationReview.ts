import type { PlanItem, Scan } from '../api/client'
import { buildProgress, type ProgressRow } from '../store/scanStore'
import { isProblemScan } from './shipmentProblems'

export function needsVerificationReview(scan: Scan, rows?: ProgressRow[]) {
  return isProblemScan(scan, null, null, rows)
    || (!scan.is_barcode && ['scanned', 'pending'].includes(scan.status))
}

export function verificationStamp(scan: Scan) {
  return JSON.stringify([scan.status, scan.verified_at, scan.verification, scan.error_message])
}

export function verificationResultsArrived(baseline: Map<string, string>, scans: Scan[]) {
  if (!baseline.size) return false
  const current = new Map(scans.map(scan => [scan.id, scan]))
  return [...baseline].every(([id, stamp]) => {
    const scan = current.get(id)
    return !scan || Boolean(scan.verification && scan.verification.source !== 'packaging_pending'
      && verificationStamp(scan) !== stamp)
  })
}

export function verificationReview(scans: Scan[], plan: PlanItem[], failed = 0) {
  const progress = buildProgress(plan, scans)
  const marks = scans.filter(scan => !scan.is_barcode)
  const attention = marks.filter(scan => needsVerificationReview(scan, progress.hasPlan ? progress.rows : undefined)).length
  const unchecked = marks.filter(scan => ['scanned', 'pending'].includes(scan.status)).length
  const hasIssues = attention > 0 || failed > 0
  const text = hasIssues
    ? `Проверка завершена. Требуют внимания: ${attention} из ${marks.length}.` +
      (unchecked || failed ? ` Не удалось проверить: ${Math.max(unchecked, failed)}.` : '')
    : `Проверка завершена. Проблем не найдено: ${marks.length} сканов.`
  return { total: marks.length, attention, unchecked, hasIssues, text }
}
