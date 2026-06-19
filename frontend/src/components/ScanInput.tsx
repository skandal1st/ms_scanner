import { useRef, useEffect, useState, useCallback, KeyboardEvent } from 'react'
import { useScanner } from '../hooks/useScanner'
import { useSerialScanner } from '../hooks/useSerialScanner'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { useScannerMode } from '../lib/scannerMode'
import { useScanStore } from '../store/scanStore'
import { CameraScanModal } from './CameraScanModal'
import { CodeSearchModal } from './CodeSearchModal'

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
  const [searchOpen, setSearchOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const { submitCode } = useScanner(documentId)
  const mode = useScannerMode()
  const isComMode = mode === 'com'
  const deleteMode = useScanStore((s) => s.deleteMode)
  const setDeleteMode = useScanStore((s) => s.setDeleteMode)
  const unpackBox = useScanStore((s) => s.unpackBox)
  const setUnpackBox = useScanStore((s) => s.setUnpackBox)

  const handleScannedCode = useCallback(
    async (code: string) => {
      const trimmed = code.trim()
      if (!trimmed) return
      setLastCode(trimmed)
      await submitCode(trimmed)
    },
    [submitCode],
  )

  const serial = useSerialScanner({
    enabled: isComMode,
    onCode: handleScannedCode,
  })

  // Keyboard wedge: фокус в input, чтобы сканер всегда туда писал.
  useEffect(() => {
    if (isComMode || cameraOpen) return
    const el = inputRef.current
    if (!el) return
    el.focus()
    const onBlur = () => setTimeout(() => el.focus(), 0)
    el.addEventListener('blur', onBlur)
    return () => el.removeEventListener('blur', onBlur)
  }, [cameraOpen, isComMode])

  // Пока фокус в поле скана — глушим шорткаты DevTools (capture: раньше дефолта Chrome).
  useEffect(() => {
    if (isComMode || !documentId || cameraOpen) return
    const onCap = (ev: globalThis.KeyboardEvent) => {
      if (document.activeElement !== inputRef.current) return
      swallowChromeInspectorKeys(ev)
    }
    window.addEventListener('keydown', onCap, true)
    return () => window.removeEventListener('keydown', onCap, true)
  }, [documentId, cameraOpen, isComMode])

  const handleKeyDown = async (e: KeyboardEvent<HTMLInputElement>) => {
    swallowChromeInspectorKeys(e.nativeEvent)
    if (e.key === 'Enter') {
      // Берём значение прямо из DOM — у быстрого сканера Enter может прийти
      // раньше, чем React успеет применить onChange и закрытие state.
      const raw = inputRef.current?.value ?? value
      const code = normalizeScannerInput(raw).trim()
      if (!code) return
      setValue('')
      await handleScannedCode(code)
    }
  }

  const inputStyle = deleteMode
    ? { borderColor: '#dc2626', background: '#fef2f2' }
    : undefined

  return (
    <div className="scan-input">
      <label className="field-label" htmlFor="scan-field">Сканирование</label>
      <div className="scan-input__row">
        {isComMode ? (
          <div
            className="scan-input__field scan-input__com-status"
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-start',
              gap: 6,
              ...(deleteMode ? { border: '2px solid #dc2626', background: '#fef2f2', padding: 6, borderRadius: 6 } : {}),
            }}
          >
            {serial.connected ? (
              <span className="badge badge--ok">🔌 COM-порт подключён</span>
            ) : (
              <span className="badge badge--warn">
                ⚠ COM-порт не подключён — настройте в разделе «Настройки»
              </span>
            )}
            {serial.error && (
              <div className="alert alert--error" style={{ width: '100%', margin: 0 }}>
                {serial.error}
              </div>
            )}
          </div>
        ) : (
          <input
            id="scan-field"
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(normalizeScannerInput(e.target.value))}
            onKeyDown={handleKeyDown}
            placeholder={deleteMode ? 'Сканируйте код для удаления…' : 'Сканируйте или введите код…'}
            disabled={!documentId}
            className="scan-input__field"
            autoComplete="off"
            spellCheck={false}
            style={inputStyle}
          />
        )}
        <button
          type="button"
          className="button scan-input__camera"
          disabled={!documentId}
          onClick={() => setDeleteMode(!deleteMode)}
          style={
            deleteMode
              ? { background: '#dc2626', borderColor: '#dc2626', color: '#fff' }
              : undefined
          }
          title={
            deleteMode
              ? 'Выключить режим удаления'
              : 'Включить режим: следующий отсканированный код будет удалён из списка'
          }
        >
          {deleteMode ? '✕ Удаление' : '🗑 Удалить'}
        </button>
        <button
          type="button"
          className="button scan-input__camera"
          disabled={!documentId}
          onClick={() => setUnpackBox(!unpackBox)}
          title={
            unpackBox
              ? 'Короб раскрывается на штучные КМ. Нажмите, чтобы сохранять короб целиком (transportpack).'
              : 'Короб сохраняется целиком (transportpack). Нажмите, чтобы раскрывать на штучные КМ.'
          }
        >
          {unpackBox ? '📦 Короб: раскрывать' : '📦 Короб: целиком'}
        </button>
        <button
          type="button"
          className="button scan-input__camera"
          disabled={!documentId}
          onClick={() => setCameraOpen(true)}
        >
          Камера
        </button>
        <button
          type="button"
          className="button scan-input__camera"
          onClick={() => setSearchOpen(true)}
          title="Найти, в каком документе уже есть марка"
        >
          🔍 Поиск марки
        </button>
      </div>
      {deleteMode && (
        <div className="alert alert--error" style={{ marginTop: 8 }}>
          Режим удаления: следующий отсканированный код будет удалён из списка.
        </div>
      )}
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
        onCode={handleScannedCode}
      />
      <CodeSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  )
}
