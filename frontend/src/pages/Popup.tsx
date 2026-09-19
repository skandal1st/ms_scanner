import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { ShipmentPage } from './Shipment'
import { AcceptancePage } from './Acceptance'
import { WriteoffPage } from './Writeoff'
import { documentsApi, type Document, type DocumentKind } from '../api/client'
import { persistUserIdFromAccessToken } from '../lib/jwt'

/**
 * Кастомное модальное окно МойСклад (кнопка «Собрать в Скандата» на документе).
 *
 * Протокол (см. docs, «Кастомные модальные окна»):
 *  1. МС грузит эту страницу как iframe с ?contextKey=… → авторизуемся через
 *     POST /auth/ms-launch (тот же путь, что и лаунчер /ms).
 *  2. МС присылает postMessage `OpenPopup` c popupParameters={msObjectId, kind},
 *     которые вернул наш обработчик кнопки (moysklad_vendor.handle_button).
 *  3. Резолвим наш Document по (kind, msObjectId) и показываем скан-сессию.
 *  4. После «Отгрузить» окно закрываем сообщением `ClosePopup` хост-окну МС.
 *
 * Web Serial (COM-сканер) внутри окна МС заблокирован Permissions-Policy — тут
 * работает keyboard-режим; COM остаётся во внешней вкладке (гибрид).
 */

interface OpenParams {
  msObjectId: string
  kind: DocumentKind
}

type State =
  | { kind: 'loading'; note: string }
  | { kind: 'shipment'; doc: Document }
  | { kind: 'acceptance'; msObjectId: string }
  | { kind: 'writeoff'; doc: Document }
  | { kind: 'error'; message: string }

const PARAMS_TIMEOUT_MS = 15000

function isMoyskladOrigin(origin: string): boolean {
  try {
    return new URL(origin).hostname.endsWith('moysklad.ru')
  } catch {
    return false
  }
}

export function PopupPage() {
  const [state, setState] = useState<State>({ kind: 'loading', note: 'Подключаемся к МойСклад…' })
  const authedRef = useRef(false)
  const paramsRef = useRef<OpenParams | null>(null)
  const resolvedRef = useRef(false)
  const msgIdRef = useRef(1)

  // Закрыть окно: сообщаем хост-окну МС. Для попапа из кнопки ответ не нужен.
  const closePopup = () => {
    try {
      window.parent?.postMessage(
        { name: 'ClosePopup', messageId: msgIdRef.current++, popupResponse: 'done' },
        '*',
      )
    } catch {
      /* ignore */
    }
  }

  // Резолвим документ, когда есть и авторизация, и параметры от OpenPopup.
  const tryResolve = async () => {
    if (resolvedRef.current) return
    if (!authedRef.current || !paramsRef.current) return
    resolvedRef.current = true
    const { msObjectId, kind } = paramsRef.current

    // Приёмка (supply) не резолвит документ заранее — он создаётся при импорте УПД;
    // передаём поступление МС как preset прямо в страницу приёмки.
    if (kind === 'supply') {
      setState({ kind: 'acceptance', msObjectId })
      return
    }
    if (kind !== 'demand' && kind !== 'loss') {
      setState({ kind: 'error', message: 'Этот тип документа не поддерживается в окне Скандаты.' })
      return
    }
    setState({ kind: 'loading', note: 'Загружаем документ…' })
    try {
      const { data: doc } = await documentsApi.resolve(msObjectId, kind)
      setState(kind === 'demand' ? { kind: 'shipment', doc } : { kind: 'writeoff', doc })
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setState({
        kind: 'error',
        message: ax?.response?.data?.detail || 'Не удалось открыть документ из МойСклад.',
      })
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const contextKey = params.get('contextKey')
    if (!contextKey) {
      setState({ kind: 'error', message: 'Окно открыто некорректно: нет contextKey.' })
      return
    }

    // 1) Авторизация по contextKey.
    fetch('/api/auth/ms-launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contextKey }),
    })
      .then(async (resp) => {
        const data = await resp.json().catch(() => ({}))
        if (!resp.ok) throw new Error(data?.detail || `HTTP ${resp.status}`)
        localStorage.setItem('access_token', data.access_token)
        localStorage.setItem('refresh_token', data.refresh_token)
        persistUserIdFromAccessToken(data.access_token)
        authedRef.current = true
        void tryResolve()
      })
      .catch((err) => {
        setState({
          kind: 'error',
          message: typeof err?.message === 'string' ? err.message : 'Ошибка авторизации через МойСклад.',
        })
      })

    // 2) Параметры от хост-окна (какой документ открыть).
    const onMessage = (ev: MessageEvent) => {
      if (!isMoyskladOrigin(ev.origin)) return
      const data = ev.data
      if (!data || data.name !== 'OpenPopup') return
      const p = data.popupParameters
      if (p && typeof p.msObjectId === 'string' && typeof p.kind === 'string') {
        paramsRef.current = { msObjectId: p.msObjectId, kind: p.kind as DocumentKind }
        void tryResolve()
      }
    }
    window.addEventListener('message', onMessage)

    // Страховка: если OpenPopup не пришёл — не висим бесконечно.
    const timeout = window.setTimeout(() => {
      if (!paramsRef.current) {
        setState({ kind: 'error', message: 'Не получены параметры документа от МойСклад. Закройте окно и попробуйте снова.' })
      }
    }, PARAMS_TIMEOUT_MS)

    return () => {
      window.removeEventListener('message', onMessage)
      clearTimeout(timeout)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (state.kind === 'loading') {
    return (
      <div style={styles.centered}>
        <div style={styles.muted}>{state.note}</div>
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div style={styles.centered}>
        <div style={styles.errorBox}>{state.message}</div>
      </div>
    )
  }

  // Встроенный режим: нужная страница с преднастроенным документом.
  // После завершения (отправка в МС / списание) окно МС закрывается через onSent.
  if (state.kind === 'shipment') {
    return <ShipmentPage embedded presetDocument={state.doc} onSent={closePopup} />
  }
  if (state.kind === 'acceptance') {
    return <AcceptancePage embedded presetMoyskladId={state.msObjectId} onSent={closePopup} />
  }
  return <WriteoffPage embedded presetDocument={state.doc} onSent={closePopup} />
}

const styles: Record<string, CSSProperties> = {
  centered: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    fontFamily: 'var(--ms-font)',
  },
  muted: { fontSize: 13, color: 'var(--ms-text-muted)' },
  errorBox: {
    color: 'var(--st-err-fg)',
    background: 'var(--st-err-bg)',
    border: '1px solid var(--st-err-bd)',
    borderRadius: 'var(--r-md)',
    padding: 16,
    maxWidth: 480,
    fontSize: 13,
  },
}
