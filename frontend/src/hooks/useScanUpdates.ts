import { useEffect, useRef } from 'react'
import { scansApi } from '../api/client'
import { useScanStore } from '../store/scanStore'
import { useDocumentLive, type DocumentEvent } from './useDocumentLive'
import { applyScanEvent } from '../lib/scanEvents'

// No COM port, keyboard handlers or focus changes: safe for embedded TSD monitoring.
export function useScanUpdates(documentId: string | null, onVerifyDone?: (failed: boolean) => void) {
  const syncing = useRef(false)
  const inflightEvents = useRef<DocumentEvent[]>([])

  const hasPending = useScanStore((s) => {
    if (!documentId) return false
    return s.scans.some((x) => x.document_id === documentId && x.status === 'pending')
  })
  // Во время пакетной проверки марок опрашиваем список как фолбэк, если WS-события
  // scan_update потерялись (статусы scanned→valid приходят по WS, но подстрахуемся).
  const verifying = useScanStore((s) => s.verifying)

  const reconcile = async () => {
    if (!documentId || syncing.current) return
    syncing.current = true
    inflightEvents.current = []
    try {
      const { data } = await scansApi.list(documentId)
      const store = useScanStore.getState()
      if (store.document?.id === documentId) store.setScans(inflightEvents.current.reduce(applyScanEvent, data))
    } catch { /* Retry on reconnect or the periodic recovery snapshot. */ }
    finally { syncing.current = false }
  }
  useDocumentLive(documentId, false, (data) => {
      if (syncing.current) inflightEvents.current.push(data)
      if (['scan_upsert', 'scan_removed', 'scans_reset'].includes(data.type)) {
        const store = useScanStore.getState()
        if (store.document?.id === documentId) store.setScans(applyScanEvent(store.scans, data))
        return
      }
      if (data.type === 'scans_changed') { void reconcile(); return }
      // Один пользователь может одновременно работать в нескольких браузерах и
      // юрлицах. Канал WS общий для аккаунта, поэтому события другой операции
      // никогда не должны менять локальную сессию.
      if (data.document_id && data.document_id !== documentId) return
      if (data.type === 'cz_token_expired') {
        useScanStore.getState().setCzTokenExpired(true)
        return
      }
      if (data.type === 'writeoff_status') {
        useScanStore.getState().setWriteoffResult({
          status: data.status,
          error: data.error_message ?? null,
        })
        return
      }
      if (data.type === 'verify_done') {
        // Пакетная проверка марок в ЧЗ завершена — один сигнал, снимаем «идёт проверка».
        // Не удалось проверить часть (таймаут/5xx ЧЗ) — они остаются «Не проверено»,
        // кнопка «Проверить марки (N)» повторит только их. Сигналим ошибкой.
        useScanStore.getState().setVerifying(false)
        onVerifyDone?.(Boolean(data.failed && data.failed > 0))
        return
      }
      if (data.type === 'scan_update') {
        const store = useScanStore.getState()
        if (!store.scans.some(scan => scan.id === data.scan_id)) { void reconcile(); return }
        if (data.owner_name) store.setCzTokenExpired(false)
        store.setScans(applyScanEvent(store.scans, data))
        // Бип на скане теперь играется сразу по ответу /scans/ (локальная проверка).
        // WS scan_update приходит из пакетной проверки — обновляем только визуально,
        // без звука на каждый код (иначе при проверке пачки — какофония).
      }
  }, () => { void reconcile() })

  // Если WS недоступен — опрашиваем список сканов, пока есть pending либо идёт проверка
  useEffect(() => {
    if (!documentId || (!hasPending && !verifying)) return
    const docId = documentId

    async function poll() {
      await reconcile()
      const store = useScanStore.getState()
      if (store.document?.id === docId && store.verifying && !store.scans.some(s => s.status === 'scanned' || s.status === 'pending')) store.setVerifying(false)
    }

    void poll()
    const interval = window.setInterval(() => void poll(), 1500)
    return () => clearInterval(interval)
  }, [documentId, hasPending, verifying])

}
