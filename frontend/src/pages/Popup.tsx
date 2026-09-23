import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { ShipmentPage } from './Shipment'
import { AcceptancePage } from './Acceptance'
import { WriteoffPage } from './Writeoff'
import { documentsApi, type Document, type DocumentKind } from '../api/client'
import { getScannerMode } from '../lib/scannerMode'
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
 * Web Serial (COM-сканер) внутри окна МС заблокирован Permissions-Policy. А
 * keyboard-режим для марок нерабочий: клавиатурный поток теряет разделитель GS
 * (0x1D) — на проде 100% invalid-сканов были без GS. Поэтому при COM-режиме
 * маркированное сканирование (отгрузка/списание) не встраивается в это окно, а
 * открывается в отдельной top-level вкладке (`/launch`), где Web Serial разрешён
 * и GS сохраняется. Вкладка/порт живут всю смену (autoCloseTab=false в COM).
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
  // COM-режим: марочное сканирование уходит во внешнюю вкладку. Признак «окно уже
  // открыли» и текст ошибки открытия — для экрана-лаунчера ниже.
  const [extScanOpened, setExtScanOpened] = useState(false)
  const [extScanError, setExtScanError] = useState<string | null>(null)

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

  // Открыть марочное сканирование в отдельной top-level вкладке (COM-режим).
  // Окно открываем СРАЗУ (иначе popup-блокер), затем берём свежий одноразовый
  // launch_token (наш access_token из этого окна МС не виден внешней вкладке —
  // это другой storage-partition) и подставляем адрес. Имя вкладки фиксированное:
  // повторные открытия переиспользуют одну вкладку, порт не плодится.
  const openExternalScan = async (kind: DocumentKind, docId: string) => {
    const mode = kind === 'demand' ? 'shipment' : 'writeoff'
    const win = window.open('', 'skandata-scan')
    try {
      const access = localStorage.getItem('access_token')
      const resp = await fetch('/api/auth/relaunch', {
        method: 'POST',
        headers: { Authorization: `Bearer ${access ?? ''}` },
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok || !data.launch_token) throw new Error('relaunch failed')
      const t = encodeURIComponent(data.launch_token)
      const d = encodeURIComponent(docId)
      if (!win) {
        setExtScanError('Браузер заблокировал новое окно. Разрешите всплывающие окна для сайта и повторите.')
        return
      }
      win.location.href = `/launch?t=${t}&mode=${mode}&doc=${d}`
      win.focus()
      setExtScanError(null)
      setExtScanOpened(true)
    } catch {
      win?.close()
      setExtScanError('Не удалось открыть окно сканирования. Повторите.')
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

  // COM-режим: марочное сканирование (отгрузка/списание) нельзя вести в этом окне
  // МС (Web Serial заблокирован Permissions-Policy). Показываем лаунчер, который
  // открывает сканирование в отдельной вкладке. Keyboard-режим оставляем встроенным
  // (там марки блокируются с подсказкой, но штрихкоды немаркированного товара идут).
  if ((state.kind === 'shipment' || state.kind === 'writeoff') && getScannerMode() === 'com') {
    const doc = state.doc
    const label = state.kind === 'shipment' ? 'отгрузку' : 'списание'
    return (
      <div style={styles.centered}>
        <div style={styles.launcher}>
          <div style={styles.launcherTitle}>COM-сканер: сканирование в отдельном окне</div>
          <div style={styles.muted}>
            Документ: <b>{doc.name}</b>
          </div>
          <p style={styles.launcherHint}>
            USB-сканеру нужно отдельное окно — в окне МойСклад браузер не даёт доступ к
            COM-порту. Марки читаются только так; клавиатурный режим их теряет.
          </p>
          <button
            type="button"
            style={styles.launcherBtn}
            onClick={() => void openExternalScan(state.kind === 'shipment' ? 'demand' : 'loss', doc.id)}
          >
            {extScanOpened ? `Открыть окно ещё раз` : `Открыть окно и начать ${label}`}
          </button>
          {extScanOpened && (
            <>
              <div style={styles.okNote}>
                Окно сканирования открыто в отдельной вкладке. Собирайте там; по «Отгрузить»
                марки уйдут в МойСклад. Это окно можно закрыть.
              </div>
              <button type="button" style={styles.launcherBtnGhost} onClick={closePopup}>
                Закрыть это окно
              </button>
            </>
          )}
          {extScanError && <div style={styles.errorBox}>{extScanError}</div>}
        </div>
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
  launcher: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    maxWidth: 460,
    width: '100%',
    textAlign: 'center',
    alignItems: 'center',
  },
  launcherTitle: { fontSize: 17, fontWeight: 700, color: 'var(--ms-text)' },
  launcherHint: { fontSize: 13, color: 'var(--ms-text-muted)', margin: 0, lineHeight: 1.5 },
  launcherBtn: {
    background: 'var(--brand)',
    color: '#fff',
    border: 'none',
    borderRadius: 'var(--r-md)',
    padding: '12px 22px',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    boxShadow: 'var(--shadow-1)',
  },
  launcherBtnGhost: {
    background: 'transparent',
    color: 'var(--ms-text-muted)',
    border: '1px solid var(--ms-border-light)',
    borderRadius: 'var(--r-md)',
    padding: '9px 18px',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
  },
  okNote: {
    fontSize: 12,
    color: 'var(--st-ok-fg, #15803d)',
    background: 'var(--st-ok-bg, #f0fdf4)',
    border: '1px solid var(--st-ok-bd, #bbf7d0)',
    borderRadius: 'var(--r-md)',
    padding: 10,
    lineHeight: 1.45,
  },
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
