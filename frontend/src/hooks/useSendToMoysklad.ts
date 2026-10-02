import { useCallback, useEffect, useRef, useState } from 'react'
import { documentsApi } from '../api/client'

/** Минимум, который хук читает у документа при опросе. */
interface PollableDoc {
  status: string
  error_message?: string | null
  processing_progress?: { sent: number; total: number; stage: string } | null
}

interface Options<T extends PollableDoc> {
  activeDocument?: (T & { id: string }) | null
  /** Получить свежий документ на опросе (documentsApi.get / acceptanceApi.getDoc). */
  fetchDoc: (id: string) => Promise<{ data: T }>
  /** Обновить локальный/сторовый стейт свежим документом на каждом опросе. */
  onPoll?: (doc: T) => void
  /** Достать текст ошибки из исключения самого /process (иначе — дефолтное сообщение). */
  extractError?: (e: unknown) => string | null
  pollIntervalMs?: number
  maxAttempts?: number
  /** Задержка перед window.close() после успеха, если вкладка открыта из МС. */
  closeTabDelayMs?: number
  /**
   * Закрывать вкладку после успеха (если открыта из МС). По умолчанию true.
   * В COM-режиме сканера ставим false: авто-закрытие вкладки закрывало бы и
   * COM-порт — на каждую отгрузку новый цикл open/close виртуального порта
   * (частая причина «залипания» COM). Держим порт открытым на всю смену.
   */
  autoCloseTab?: boolean
}

/**
 * Общий флоу «Отправить в МойСклад» для отгрузки и приёмки: дёргает
 * `POST /documents/{id}/process`, затем опрашивает документ до `accepted` либо
 * до появления `error_message` (напр. «нет на складе» / истёк токен ЧЗ), чтобы
 * не закрыть вкладку с ложным «Готово». При успехе, если вкладка открыта из МС
 * (`window.opener`), закрывает её.
 *
 * - `done` — документ принят (для success-оверлея на любой странице).
 * - `closingTab` — принят И вкладка будет закрыта (для «Возвращаемся в МойСклад…»).
 */
export function useSendToMoysklad<T extends PollableDoc>(opts: Options<T>) {
  const {
    fetchDoc,
    onPoll,
    extractError,
    pollIntervalMs = 1500,
    maxAttempts = 800,
    closeTabDelayMs = 1200,
    autoCloseTab = true,
  } = opts

  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [closingTab, setClosingTab] = useState(false)
  const runRef = useRef(0)
  const sendingRef = useRef(false)
  const [progress, setProgress] = useState<PollableDoc["processing_progress"]>(null)

  useEffect(
    () => () => {
      // Опрос старой операции может завершиться уже после повторного OpenPopup.
      // Инвалидируем его, чтобы onPoll не вернул предыдущий документ в общий store.
      runRef.current += 1
      sendingRef.current = false
    },
    [],
  )

  const reset = useCallback(() => {
    runRef.current += 1
    sendingRef.current = false
    setSending(false)
    setProgress(null)
    setError(null)
    setDone(false)
    setClosingTab(false)
  }, [])

  const send = useCallback(
    async (docId: string, resume = false) => {
      if (sendingRef.current) return
      sendingRef.current = true
      const runId = ++runRef.current
      setSending(true)
      setError(null)
      setDone(false)
      try {
        if (!resume) await documentsApi.process(docId)
        let finalStatus = 'processing'
        let failReason: string | null = null
        let networkFailures = 0
        for (let i = 0; i < maxAttempts; i++) {
          await new Promise((r) => setTimeout(r, pollIntervalMs))
          if (runRef.current !== runId) return
          let fresh: T
          try {
            fresh = (await fetchDoc(docId)).data
            networkFailures = 0
          } catch (e) {
            if (++networkFailures < 5) continue
            throw e
          }
          if (runRef.current !== runId) return
          setProgress(fresh.processing_progress ?? null)
          finalStatus = fresh.status
          onPoll?.(fresh)
          if (fresh.status === 'accepted') break
          // Воркер выставил причину неуспеха — прекращаем опрос и показываем её,
          // а не ждём ложное «ещё обрабатывается».
          if (fresh.status !== "processing" && fresh.error_message) {
            failReason = fresh.error_message
            break
          }
        }
        if (runRef.current !== runId) return
        if (failReason) {
          setError(failReason)
        } else if (finalStatus === 'accepted') {
          setDone(true)
          if (autoCloseTab && window.opener && !window.opener.closed) {
            setClosingTab(true)
            setTimeout(() => {
              if (runRef.current === runId) window.close()
            }, closeTabDelayMs)
          }
        } else {
          setError(
            'МойСклад ещё обрабатывает документ. Обновите страницу позже, чтобы увидеть результат.',
          )
        }
      } catch (e) {
        if (runRef.current !== runId) return
        setError(
          extractError?.(e) ??
            ((e as { response?: { data?: { detail?: string } } }).response?.data?.detail) ??
            'Не удалось отправить документ в МойСклад. Попробуйте ещё раз.',
        )
      } finally {
        if (runRef.current === runId) {
          sendingRef.current = false
          setSending(false)
        }
      }
    },
    [fetchDoc, onPoll, extractError, pollIntervalMs, maxAttempts, closeTabDelayMs, autoCloseTab],
  )

  const sendRef = useRef(send)
  sendRef.current = send
  const activeId = opts.activeDocument?.id
  const activeStatus = opts.activeDocument?.status
  const previousId = useRef(activeId)
  useEffect(() => {
    if (previousId.current !== activeId) {
      previousId.current = activeId
      reset()
    }
    if (activeId && activeStatus === 'processing' && !sendingRef.current) {
      void sendRef.current(activeId, true)
    }
  }, [activeId, activeStatus, reset])

  const progressLabel = progress?.stage === 'sending' && progress.total > 0
    ? `Передано ${progress.sent}/${progress.total} марок…`
    : 'Отправка в МС…'
  return { send, sending, error, done, closingTab, setError, reset, progressLabel }
}
