import { useCallback, useState } from 'react'
import { documentsApi } from '../api/client'

/** Минимум, который хук читает у документа при опросе. */
interface PollableDoc {
  status: string
  error_message?: string | null
}

interface Options<T extends PollableDoc> {
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
    maxAttempts = 20,
    closeTabDelayMs = 1200,
  } = opts

  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [closingTab, setClosingTab] = useState(false)

  const reset = useCallback(() => {
    setError(null)
    setDone(false)
    setClosingTab(false)
  }, [])

  const send = useCallback(
    async (docId: string) => {
      setSending(true)
      setError(null)
      setDone(false)
      try {
        await documentsApi.process(docId)
        let finalStatus = 'processing'
        let failReason: string | null = null
        for (let i = 0; i < maxAttempts; i++) {
          await new Promise((r) => setTimeout(r, pollIntervalMs))
          const { data: fresh } = await fetchDoc(docId)
          finalStatus = fresh.status
          onPoll?.(fresh)
          if (fresh.status === 'accepted') break
          // Воркер выставил причину неуспеха — прекращаем опрос и показываем её,
          // а не ждём ложное «ещё обрабатывается».
          if (fresh.error_message) {
            failReason = fresh.error_message
            break
          }
        }
        if (failReason) {
          setError(failReason)
        } else if (finalStatus === 'accepted') {
          setDone(true)
          if (window.opener && !window.opener.closed) {
            setClosingTab(true)
            setTimeout(() => window.close(), closeTabDelayMs)
          }
        } else {
          setError(
            'МойСклад ещё обрабатывает документ. Обновите страницу позже, чтобы увидеть результат.',
          )
        }
      } catch (e) {
        setError(
          extractError?.(e) ??
            'Не удалось отправить документ в МойСклад. Попробуйте ещё раз.',
        )
      } finally {
        setSending(false)
      }
    },
    [fetchDoc, onPoll, extractError, pollIntervalMs, maxAttempts, closeTabDelayMs],
  )

  return { send, sending, error, done, closingTab, setError, reset }
}
