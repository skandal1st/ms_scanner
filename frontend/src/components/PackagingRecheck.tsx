import { useState } from 'react'
import { scansApi, tsdApi, type Scan } from '../api/client'

export function PackagingRecheck({ scan, terminal = false, disabled = false, onQueued }: {
  scan: Scan; terminal?: boolean; disabled?: boolean; onQueued: (scan: Scan) => void
}) {
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const pending = scan.status === 'pending' && scan.verification?.source === 'packaging_recheck'
  if (scan.is_barcode || scan.status === 'used_in_other_doc') return null
  const request = async () => {
    setSending(true)
    setError('')
    try {
      const { data } = terminal
        ? await tsdApi.recheckPackaging(scan.document_id, scan.id)
        : await scansApi.recheckPackaging(scan.id)
      onQueued(data)
    } catch (err: any) {
      setError(String(err.response?.data?.detail || 'Не удалось запустить уточнение в ЧЗ. Повторите попытку.'))
    } finally { setSending(false) }
  }
  return <div className={terminal ? 'tsd-pack-control' : 'scans-expanded__row'}>
    <button type="button" className={terminal ? 'tsd-button' : 'button'}
      disabled={disabled || sending || scan.status === 'pending'} aria-busy={sending || pending}
      onClick={() => void request()}>{sending || pending ? 'Уточняем упаковку в ЧЗ…' : 'Уточнить упаковку в ЧЗ'}</button>
    <span className="hint" role="status">{pending ? 'Проверка идёт в фоне. Можно продолжать сканирование.'
      : scan.verification?.packaging_message || 'ЧЗ определит пачку, блок или короб. Подтверждённый тип сохранится для следующих сканов.'}</span>
    {error && <span role="alert" className="text-error">{error}</span>}
    {terminal && scan.verification?.packaging_request_id && scan.error_message
      && <span role="alert" className="text-error">{scan.error_message}</span>}
  </div>
}
