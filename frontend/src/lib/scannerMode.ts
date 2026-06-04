import { useEffect, useState } from 'react'

export type ScannerMode = 'keyboard' | 'com'

const STORAGE_KEY = 'scanner_mode'
const CHANGE_EVENT = 'scanner-mode-change'

export function getScannerMode(): ScannerMode {
  const raw = localStorage.getItem(STORAGE_KEY)
  return raw === 'com' ? 'com' : 'keyboard'
}

export function setScannerMode(mode: ScannerMode): void {
  localStorage.setItem(STORAGE_KEY, mode)
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: mode }))
}

export function useScannerMode(): ScannerMode {
  const [mode, setMode] = useState<ScannerMode>(() => getScannerMode())

  useEffect(() => {
    const onChange = () => setMode(getScannerMode())
    // storage event — для синхронизации между вкладками
    window.addEventListener('storage', onChange)
    // custom event — для синхронизации внутри текущей вкладки
    window.addEventListener(CHANGE_EVENT, onChange)
    return () => {
      window.removeEventListener('storage', onChange)
      window.removeEventListener(CHANGE_EVENT, onChange)
    }
  }, [])

  return mode
}

export function isWebSerialSupported(): boolean {
  return typeof navigator !== 'undefined' && 'serial' in navigator
}
