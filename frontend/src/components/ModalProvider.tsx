import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

type Variant = 'info' | 'error' | 'warn' | 'success'

interface AlertOptions {
  title?: string
  variant?: Variant
  okText?: string
}

interface ConfirmOptions extends AlertOptions {
  cancelText?: string
  danger?: boolean
}

export interface ModalApi {
  /** Замена window.alert: показывает модалку с одной кнопкой, резолвится по закрытию. */
  alert: (message: ReactNode, opts?: AlertOptions) => Promise<void>
  /** Замена window.confirm: резолвится true (ОК) / false (отмена/крестик/оверлей/Esc). */
  confirm: (message: ReactNode, opts?: ConfirmOptions) => Promise<boolean>
}

interface DialogState extends ConfirmOptions {
  kind: 'alert' | 'confirm'
  message: ReactNode
  resolve: (v: unknown) => void
}

const ModalContext = createContext<ModalApi | null>(null)

const DEFAULT_TITLE: Record<Variant, string> = {
  info: 'Сообщение',
  error: 'Ошибка',
  warn: 'Внимание',
  success: 'Готово',
}

export function useModal(): ModalApi {
  const ctx = useContext(ModalContext)
  if (!ctx) throw new Error('useModal должен использоваться внутри <ModalProvider>')
  return ctx
}

export function ModalProvider({ children }: { children: ReactNode }) {
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const queueRef = useRef<DialogState[]>([])

  const push = useCallback((d: DialogState) => {
    setDialog((cur) => {
      if (cur) {
        queueRef.current.push(d)
        return cur
      }
      return d
    })
  }, [])

  const close = useCallback((result: unknown) => {
    setDialog((cur) => {
      cur?.resolve(result)
      return queueRef.current.shift() ?? null
    })
  }, [])

  const alert = useCallback(
    (message: ReactNode, opts: AlertOptions = {}) =>
      new Promise<void>((resolve) => {
        push({ kind: 'alert', message, ...opts, resolve: () => resolve() })
      }),
    [push],
  )

  const confirm = useCallback(
    (message: ReactNode, opts: ConfirmOptions = {}) =>
      new Promise<boolean>((resolve) => {
        push({ kind: 'confirm', message, ...opts, resolve: (v) => resolve(Boolean(v)) })
      }),
    [push],
  )

  // Esc закрывает: alert → ок, confirm → отмена (false).
  useEffect(() => {
    if (!dialog) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(dialog.kind === 'confirm' ? false : undefined)
      if (e.key === 'Enter' && dialog.kind === 'alert') close(undefined)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dialog, close])

  const api = useMemo<ModalApi>(() => ({ alert, confirm }), [alert, confirm])

  const variant = dialog?.variant ?? (dialog?.kind === 'confirm' ? 'warn' : 'info')
  const title = dialog?.title ?? DEFAULT_TITLE[variant]
  const isConfirm = dialog?.kind === 'confirm'
  const dismiss = () => close(isConfirm ? false : undefined)

  return (
    <ModalContext.Provider value={api}>
      {children}
      {dialog && (
        <div className="popup">
          <div className="popup__overlay" onClick={dismiss} />
          <dialog className="popup__body" open>
            <button type="button" className="popup__close" onClick={dismiss} aria-label="Закрыть" />
            <div className="popup__title">{title}</div>
            <div className="popup__content">
              <div
                className={`alert alert--${variant === 'error' ? 'error' : variant === 'success' ? 'ok' : variant === 'warn' ? 'warn' : 'info'}`}
                style={{ marginTop: 0, whiteSpace: 'pre-wrap' }}
              >
                {dialog.message}
              </div>
            </div>
            <div className="buttons" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
              {isConfirm && (
                <button type="button" className="button" onClick={() => close(false)}>
                  {dialog.cancelText ?? 'Отмена'}
                </button>
              )}
              <button
                type="button"
                className={`button ${dialog.danger ? 'button--danger' : 'button--primary'}`}
                onClick={() => close(isConfirm ? true : undefined)}
                autoFocus
              >
                {dialog.okText ?? (isConfirm ? 'Подтвердить' : 'ОК')}
              </button>
            </div>
          </dialog>
        </div>
      )}
    </ModalContext.Provider>
  )
}
