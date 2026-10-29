import { useEffect, useRef, useState, type RefObject } from 'react'
import { useTsdScannerFocus } from '../hooks/useTsdScannerFocus'
import { Icon } from './Icon'

interface Props {
  inputRef: RefObject<HTMLInputElement>
  blocked: boolean
  online: boolean
  collectionStarted: boolean
  pending: boolean
  messageKind?: 'ok' | 'error'
  resetVersion: number
  onHasCodeChange: (hasCode: boolean) => void
  onScan: (code: string) => void
}

// Keep keyboard/IME updates local: a mark can contain dozens of characters,
// and none of them should re-render the document's positions and scans.
export function TsdScanInput({ inputRef, blocked, online, collectionStarted, pending,
  messageKind, resetVersion, onHasCodeChange, onScan }: Props) {
  const [code, setCode] = useState('')
  const codeRef = useRef('')
  const scanRef = useRef(onScan)
  scanRef.current = onScan

  const changeCode = (value: string) => {
    const hadCode = Boolean(codeRef.current.trim())
    codeRef.current = value
    setCode(value)
    const hasCode = Boolean(value.trim())
    if (hadCode !== hasCode) onHasCodeChange(hasCode)
  }
  useEffect(() => {
    codeRef.current = ''
    setCode('')
    onHasCodeChange(false)
  }, [resetVersion, onHasCodeChange])

  useTsdScannerFocus(inputRef, blocked, value => changeCode(codeRef.current + value))
  useEffect(() => {
    // Preserve the fallback for scanners that paste a code without Enter.
    if (!code.trim() || !online || blocked) return
    const timer = window.setTimeout(() => scanRef.current(codeRef.current), 500)
    return () => window.clearTimeout(timer)
  }, [code, online, blocked])

  return <form className={`tsd-scan-box tsd-scan-box--compact ${messageKind ? `tsd-scan-box--${messageKind}` : ''}`}
    onSubmit={event => { event.preventDefault(); scanRef.current(codeRef.current) }}>
    <label htmlFor="tsd-scan-input"><Icon name="scan" size={20} /><span className="tsd-visually-hidden">Сканируйте штрихкод</span></label>
    <input id="tsd-scan-input" ref={inputRef} value={code} onChange={event => changeCode(event.target.value)}
      readOnly={blocked} inputMode="text" placeholder={!collectionStarted ? 'Сначала начните сборку' : pending ? 'Записываем скан…' : 'Сканируйте марку'}
      autoComplete="off" autoCapitalize="off" spellCheck={false} enterKeyHint="send" />
    <button type="submit" className="tsd-button" aria-label="Принять код" disabled={!online || !code.trim() || blocked}><Icon name="check" size={20} /></button>
  </form>
}
