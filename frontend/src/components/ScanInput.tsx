import { useRef, useEffect, useState, useCallback, KeyboardEvent } from 'react'
import { useScanner } from '../hooks/useScanner'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { CameraScanModal } from './CameraScanModal'

interface Props {
  documentId: string | null
}

/** Часть USB-сканеров шлёт префиксом Ctrl+Shift+I/J/C — в Chrome открывается DevTools. */
function swallowChromeInspectorKeys(ev: globalThis.KeyboardEvent): boolean {
  if (ev.key === 'F12' || ev.key === 'F11') {
    ev.preventDefault()
    ev.stopPropagation()
    return true
  }
  if (ev.ctrlKey && ev.shiftKey) {
    const k = ev.key.length === 1 ? ev.key.toUpperCase() : ev.key
    if (k === 'I' || k === 'J' || k === 'C' || k === 'K') {
      ev.preventDefault()
      ev.stopPropagation()
      return true
    }
  }
  if (ev.ctrlKey && !ev.shiftKey && !ev.metaKey && (ev.key === 'u' || ev.key === 'U')) {
    ev.preventDefault()
    ev.stopPropagation()
    return true
  }
  if (ev.metaKey && ev.altKey && (ev.key === 'i' || ev.key === 'I')) {
    ev.preventDefault()
    ev.stopPropagation()
    return true
  }
  return false
}

export function ScanInput({ documentId }: Props) {
  const [value, setValue] = useState('')
  const [lastCode, setLastCode] = useState('')
  const [cameraOpen, setCameraOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const { submitCode } = useScanner(documentId)

  useEffect(() => {
    if (cameraOpen) return
    const el = inputRef.current
    if (!el) return
    el.focus()
    const onBlur = () => setTimeout(() => el.focus(), 0)
    el.addEventListener('blur', onBlur)
    return () => el.removeEventListener('blur', onBlur)
  }, [cameraOpen])

  // Пока фокус в поле скана — глушим шорткаты DevTools (capture: раньше дефолта Chrome).
  useEffect(() => {
    if (!documentId || cameraOpen) return
    const onCap = (ev: globalThis.KeyboardEvent) => {
      if (document.activeElement !== inputRef.current) return
      swallowChromeInspectorKeys(ev)
    }
    window.addEventListener('keydown', onCap, true)
    return () => window.removeEventListener('keydown', onCap, true)
  }, [documentId, cameraOpen])

  const handleKeyDown = async (e: KeyboardEvent<HTMLInputElement>) => {
    swallowChromeInspectorKeys(e.nativeEvent)
    if (e.key === 'Enter') {
      // Берём значение прямо из DOM — у быстрого сканера Enter может прийти
      // раньше, чем React успеет применить onChange и закрытие state.
      const raw = inputRef.current?.value ?? value
      const code = normalizeScannerInput(raw).trim()
      if (!code) return
      setLastCode(code)
      setValue('')
      await submitCode(code)
    }
  }

  const handleCameraCode = useCallback(
    async (code: string) => {
      setLastCode(code)
      await submitCode(code)
    },
    [submitCode],
  )

  return (
    <div className="scan-input">
      <label className="field-label" htmlFor="scan-field">Сканирование</label>
      <div className="scan-input__row">
        <input
          id="scan-field"
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(normalizeScannerInput(e.target.value))}
          onKeyDown={handleKeyDown}
          placeholder="Сканируйте или введите код…"
          disabled={!documentId}
          className="scan-input__field"
          autoComplete="off"
          spellCheck={false}
        />
        <button
          type="button"
          className="button scan-input__camera"
          disabled={!documentId}
          onClick={() => setCameraOpen(true)}
        >
          Камера
        </button>
      </div>
      {lastCode && (
        <div className="scan-input__last">
          <span>Последний:</span>
          <code>{lastCode.slice(0, 30)}{lastCode.length > 30 ? '…' : ''}</code>
        </div>
      )}
      {!documentId && (
        <p className="hint">Выберите документ для начала сканирования</p>
      )}
      <CameraScanModal
        open={cameraOpen}
        onClose={() => setCameraOpen(false)}
        onCode={handleCameraCode}
      />
    </div>
  )
}
