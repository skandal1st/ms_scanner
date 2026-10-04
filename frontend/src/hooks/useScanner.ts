import { useCallback } from 'react'
import { scansApi, isSscc, type Scan } from '../api/client'
import { useScanStore } from '../store/scanStore'
import { useModal } from '../components/ModalProvider'
import { useScanUpdates } from './useScanUpdates'

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
  useScanUpdates(documentId, failed => playBeep(failed ? 'error' : 'ok'))

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
