import type { Scan } from '../api/client'
import type { DocumentEvent } from '../hooks/useDocumentLive'

/** Discovery may finish over WS before the initial POST reaches the terminal. */
export function preferResolvedScan(existing: Scan | undefined, incoming: Scan): Scan {
  const pendingSource = incoming.verification?.source
  if (existing && pendingSource === 'packaging_recheck'
      && existing.verification?.packaging_request_id === incoming.verification?.request_id) return existing
  if (existing && ['sscc_pending', 'packaging_pending'].includes(pendingSource || '')
      && (existing.status !== 'pending' || existing.box_quantity != null)) {
    return { ...existing, duplicate: incoming.duplicate }
  }
  return incoming
}

export function applyScanEvent(scans: Scan[], event: DocumentEvent): Scan[] {
  if (event.type === 'scans_reset') return []
  if (event.type === 'scan_removed') return scans.filter(item => item.id !== event.scan_id)
  if (event.type === 'scan_upsert') {
    const incoming = event.scan as Scan
    const value = preferResolvedScan(scans.find(item => item.id === incoming.id), incoming)
    const found = scans.some(item => item.id === value.id)
    return found ? scans.map(item => item.id === value.id ? value : item) : [value, ...scans]
  }
  if (event.type === 'scan_update') {
    const { type: _type, scan_id, document_id: _documentId, ...data } = event
    const patch = Object.fromEntries(Object.entries(data).filter(([key, value]) => value != null || ['error_message', 'product_name', 'owner_name', 'owner_inn', 'withdraw_reason', 'verification', 'verified_at'].includes(key)))
    return scans.map(item => item.id === scan_id ? { ...item, ...patch } : item)
  }
  return scans
}
