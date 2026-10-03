import { useEffect, useRef, type RefObject } from 'react'
import { bindTsdScannerFocus } from '../lib/tsdScannerFocus'

export function useTsdScannerFocus(input: RefObject<HTMLInputElement>, blocked: boolean, append: (value: string) => void) {
  const latest = useRef({ blocked, append })
  latest.current = { blocked, append }
  useEffect(() => bindTsdScannerFocus({
    input: () => input.current,
    blocked: () => latest.current.blocked,
    append: (value) => latest.current.append(value),
  }), [input])
  useEffect(() => {
    if (!blocked) input.current?.focus({ preventScroll: true })
  }, [input, blocked])
}
