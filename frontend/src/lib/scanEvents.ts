import type { Scan } from '../api/client'
import type { DocumentEvent } from '../hooks/useDocumentLive'

export function applyScanEvent(scans: Scan[], event: DocumentEvent): Scan[] {
  if (event.type === 'scans_reset') return []
  if (event.type === 'scan_removed') return scans.filter(item => item.id !== event.scan_id)
  if (event.type === 'scan_upsert') {
    const value = event.scan as Scan
    const found = scans.some(item => item.id === value.id)
    return found ? scans.map(item => item.id === value.id ? value : item) : [value, ...scans]
  }
  if (event.type === 'scan_update') {
    const { type: _type, scan_id, document_id: _documentId, ...data } = event
    const patch = Object.fromEntries(Object.entries(data).filter(([key, value]) => value != null || ['error_message', 'product_name'].includes(key)))
    return scans.map(item => item.id === scan_id ? { ...item, ...patch } : item)
  }
  return scans
}
