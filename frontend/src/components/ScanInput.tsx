import { useRef, useEffect, useState, useCallback, KeyboardEvent } from 'react'
import { useScanner } from '../hooks/useScanner'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { CameraScanModal } from './CameraScanModal'

interface Props {
  documentId: string | null
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

  const handleKeyDown = async (e: KeyboardEvent<HTMLInputElement>) => {
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
