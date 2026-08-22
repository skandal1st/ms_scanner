import { useEffect, useRef, useCallback } from 'react'
import { scansApi, isSscc } from '../api/client'
import { useScanStore } from '../store/scanStore'
import { decodeJwtSub } from '../lib/jwt'

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

function resolveWsUserId(): string | null {
  const cached = localStorage.getItem('user_id')
  if (cached) return cached
  const token = localStorage.getItem('access_token')
  if (!token) return null
  const sub = decodeJwtSub(token)
  if (sub) {
    localStorage.setItem('user_id', sub)
    return sub
  }
  return null
}

export function useScanner(documentId: string | null) {
  const { addScan, updateScan, flashScan } = useScanStore()
  const wsRef = useRef<WebSocket | null>(null)

  const hasPending = useScanStore((s) => {
    if (!documentId) return false
    return s.scans.some((x) => x.document_id === documentId && x.status === 'pending')
  })
  // Во время пакетной проверки марок опрашиваем список как фолбэк, если WS-события
  // scan_update потерялись (статусы scanned→valid приходят по WS, но подстрахуемся).
  const verifying = useScanStore((s) => s.verifying)

  // WebSocket — обновления статусов от Celery
  useEffect(() => {
    const userId = resolveWsUserId()
    if (!userId) return

    const wsUrl = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/${userId}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
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
        // ЧЗ вернул данные (владелец) → токен рабочий, снимаем баннер.
        if (data.owner_name) useScanStore.getState().setCzTokenExpired(false)
        updateScan(data.scan_id, {
          status: data.status,
          product_name: data.product_name,
          error_message: data.error_message,
          ...(data.gtin != null && data.gtin !== ''
            ? { gtin: data.gtin as string }
            : {}),
          ...(data.moysklad_product_id != null && data.moysklad_product_id !== ''
            ? { moysklad_product_id: data.moysklad_product_id as string }
            : {}),
          ...(typeof data.is_box === 'boolean' ? { is_box: data.is_box } : {}),
          ...(data.box_quantity != null
            ? { box_quantity: data.box_quantity as number }
            : {}),
          ...(data.owner_name != null ? { owner_name: data.owner_name as string } : {}),
          ...(data.producer_name != null
            ? { producer_name: data.producer_name as string }
            : {}),
          ...(data.owner_inn != null ? { owner_inn: data.owner_inn as string } : {}),
          ...(typeof data.withdrawn === 'boolean' ? { withdrawn: data.withdrawn } : {}),
          ...(data.withdraw_reason != null
            ? { withdraw_reason: data.withdraw_reason as string }
            : {}),
          ...(Array.isArray(data.child_codes)
            ? { child_codes: data.child_codes as string[] }
            : {}),
        })
        // Бип на скане теперь играется сразу по ответу /scans/ (локальная проверка).
        // WS scan_update приходит из пакетной проверки — обновляем только визуально,
        // без звука на каждый код (иначе при проверке пачки — какофония).
      }
    }

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping')
    }, 30000)

    return () => {
      clearInterval(ping)
      ws.close()
    }
  }, [updateScan])

  // Если WS недоступен — опрашиваем список сканов, пока есть pending либо идёт проверка
  useEffect(() => {
    if (!documentId || (!hasPending && !verifying)) return
    const docId = documentId

    async function poll() {
      try {
        const { data } = await scansApi.list(docId)
        const store = useScanStore.getState()
        store.setScans(data)
        // Подстраховка на случай потерянного WS-события verify_done: если проверка
        // шла и непроверенных марок больше не осталось — снимаем флаг.
        if (
          store.verifying &&
          !data.some((s) => s.document_id === docId && s.status === 'scanned')
        ) {
          store.setVerifying(false)
        }
      } catch {
        /* сеть / 401 обработает axios */
      }
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
          window.alert('Код не найден в этом документе')
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
        if (typeof d === 'string' && d) window.alert(d)
        console.error('Scan error:', err)
      }
    },
    [documentId, addScan, flashScan]
  )

  return { submitCode }
}
