import { useEffect, useRef, useCallback } from 'react'
import { scansApi, isSscc, type Scan } from '../api/client'
import { useScanStore } from '../store/scanStore'
import { useModal } from '../components/ModalProvider'
import { useDocumentLive, type DocumentEvent } from './useDocumentLive'
import { applyScanEvent } from '../lib/scanEvents'

const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)()

// Одиночный тон.
function tone(freq: number, start: number, dur: number, wave: OscillatorType = 'sine') {
  const osc = audioCtx.createOscillator()
  const gain = audioCtx.createGain()
  osc.connect(gain)
  gain.connect(audioCtx.destination)
  osc.type = wave
  osc.frequency.value = freq
  gain.gain.setValueAtTime(0.3, start)
  gain.gain.exponentialRampToValueAtTime(0.001, start + dur)
  osc.start(start)
  osc.stop(start + dur)
}

// 'ok' — высокий короткий, 'error' — низкий длинный, 'unknown' — отдельный
// характерный двойной сигнал для несопоставленной позиции (валидный КМ, но
// товар не найден в плане/каталоге — кладовщику нужно сопоставить вручную).
function playBeep(type: 'ok' | 'error' | 'unknown') {
  const now = audioCtx.currentTime
  if (type === 'ok') {
    tone(880, now, 0.15)
  } else if (type === 'error') {
    tone(220, now, 0.4)
  } else {
    // Двойной «вопросительный» блип: восходящая пара, тембр square — не спутать
    // ни с успехом, ни с ошибкой.
    tone(500, now, 0.12, 'square')
    tone(760, now + 0.16, 0.14, 'square')
  }
}

export function useScanner(documentId: string | null) {
  const modal = useModal()
  const { addScan, flashScan } = useScanStore()
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
        playBeep(data.failed && data.failed > 0 ? 'error' : 'ok')
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

  const submitCode = useCallback(
    async (code: string) => {
      if (!documentId || !code.trim()) return
      const trimmed = code.trim()
      const state = useScanStore.getState()

      // Режим удаления: ищем существующий скан с тем же кодом и удаляем его.
      if (state.deleteMode) {
        const existing = state.scans.find(
          (s) => s.document_id === documentId && s.code === trimmed,
        )
        if (!existing) {
          playBeep('error')
          void modal.alert('Код не найден в этом документе', { variant: 'warn' })
          return
        }
        try {
          await scansApi.delete(existing.id)
          state.removeScan(existing.id)
          playBeep('ok')
        } catch (err) {
          playBeep('error')
          console.error('Delete scan error:', err)
        }
        return
      }

      try {
        // SSCC-короб идёт в отдельный эндпоинт: раскрыть на штучные КМ либо сохранить целиком.
        if (isSscc(trimmed)) {
          const { data: boxScans } = await scansApi.box(
            documentId,
            trimmed,
            state.unpackBox,
          )
          let hadError = false
          boxScans.forEach((s) => {
            if (s.duplicate) {
              // Повторный скан в этом документе — строку не добавляем, подсвечиваем существующую.
              flashScan(s.id)
              hadError = true
            } else {
              addScan(s)
              if (s.status === 'used_in_other_doc') hadError = true
            }
          })
          if (hadError) playBeep('error')
          return
        }

        const targetPid = state.targetProductId
        const { data: scan } = await scansApi.create(
          documentId,
          trimmed,
          targetPid || undefined
        )
        if (scan.duplicate) {
          // Код уже есть в этом документе: не дублируем строку, подсвечиваем её.
          flashScan(scan.id)
          playBeep('error')
          return
        }
        addScan(scan)
        // Основной флоу: КМ проверяется локально при скане, статус приходит сразу в
        // ответе (scanned = принят локально; invalid = кривой формат). Бип — здесь,
        // мгновенно, без ожидания ЧЗ. Проверка в ЧЗ — потом, по кнопке «Проверить марки».
        if (scan.status === 'invalid' || scan.status === 'used_in_other_doc')
          playBeep('error')
        else playBeep('ok')
      } catch (err: unknown) {
        playBeep('error')
        const ax = err as { response?: { data?: { detail?: unknown } } }
        const d = ax?.response?.data?.detail
        if (typeof d === 'string' && d) void modal.alert(d, { variant: 'error' })
        console.error('Scan error:', err)
      }
    },
    [documentId, addScan, flashScan, modal]
  )

  return { submitCode }
}
