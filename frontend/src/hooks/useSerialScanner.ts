import { useCallback, useEffect, useRef, useState } from 'react'
import { SerialLineBuffer, normalizeGs } from '../lib/serialLineBuffer'
import { isWebSerialSupported } from '../lib/scannerMode'

// Хардкод параметров порта: дефолт большинства промышленных сканеров (Honeywell/Datalogic/Zebra).
// Если конкретная модель отличается — настраивается сервисными штрих-кодами из мануала сканера.
const PORT_OPTIONS: SerialOptions = {
  baudRate: 9600,
  dataBits: 8,
  stopBits: 1,
  parity: 'none',
  flowControl: 'none',
}

// Распознаём типовые ошибки открытия порта и даём кладовщику понятный русский текст.
// Chrome при занятом порте: DOMException NetworkError "Failed to open serial port."
export function humanizeSerialOpenError(e: unknown): string {
  const name = e instanceof DOMException ? e.name : ''
  const msg = e instanceof Error ? e.message : String(e)
  const low = msg.toLowerCase()
  if (
    name === 'NetworkError' ||
    low.includes('failed to open serial port') ||
    low.includes('already open') ||
    low.includes('access is denied') ||
    low.includes('access denied')
  ) {
    return 'COM-порт занят другой программой или вкладкой. Закройте другие окна приложения и программы, использующие сканер, затем подключитесь снова.'
  }
  return `Не удалось открыть COM-порт: ${msg}`
}

interface UseSerialScannerArgs {
  enabled: boolean
  onCode: (code: string) => void
}

interface UseSerialScannerResult {
  supported: boolean
  connected: boolean
  error: string | null
  requestConnect: () => Promise<void>
  disconnect: () => Promise<void>
}

export function useSerialScanner({ enabled, onCode }: UseSerialScannerArgs): UseSerialScannerResult {
  const [supported] = useState<boolean>(() => isWebSerialSupported())
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const portRef = useRef<SerialPort | null>(null)
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)
  const aliveRef = useRef(false)
  // onCode передаётся через ref, чтобы изменение callback не перезапускало reader-loop.
  const onCodeRef = useRef(onCode)
  useEffect(() => {
    onCodeRef.current = onCode
  }, [onCode])

  const stopReader = useCallback(async () => {
    aliveRef.current = false
    const reader = readerRef.current
    readerRef.current = null
    if (reader) {
      try {
        await reader.cancel()
      } catch {
        /* ignore */
      }
      try {
        reader.releaseLock()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const closePort = useCallback(async () => {
    await stopReader()
    const port = portRef.current
    portRef.current = null
    if (port) {
      try {
        await port.close()
      } catch {
        /* ignore — порт мог быть уже отключён физически */
      }
    }
    setConnected(false)
  }, [stopReader])

  const startReader = useCallback(async (port: SerialPort) => {
    if (!port.readable) {
      setError('COM-порт не доступен для чтения')
      return
    }
    const reader = port.readable.getReader()
    readerRef.current = reader
    aliveRef.current = true
    const buffer = new SerialLineBuffer()
    // latin1, НЕ utf-8: сканер шлёт байты GS1 DataMatrix как есть (включая GS-подмену
    // 0xF8). UTF-8 ломал бы невалидные байты на U+FFFD; latin1 маппит байт↔кодпоинт 1:1.
    const decoder = new TextDecoder('latin1')

    try {
      while (aliveRef.current) {
        const { value, done } = await reader.read()
        if (done) break
        if (!value) continue
        const text = decoder.decode(value, { stream: true })
        const lines = buffer.feed(text)
        for (const line of lines) {
          // normalizeGs: подменный GS-байт сканера (0xF8) → каноничный 0x1D.
          const code = normalizeGs(line).trim()
          if (code) onCodeRef.current(code)
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      console.error('[useSerialScanner] reader loop error:', e)
      setError(`Ошибка чтения COM-порта: ${msg}`)
    } finally {
      try {
        reader.releaseLock()
      } catch {
        /* ignore */
      }
    }
  }, [])

  const openAndRead = useCallback(
    async (port: SerialPort) => {
      // Если порт уже открыт (например, мы же его и держим) — port.readable не null.
      // Повторный open даёт InvalidStateError, поэтому пропускаем.
      const alreadyOpen = port.readable !== null
      if (!alreadyOpen) {
        try {
          await port.open(PORT_OPTIONS)
        } catch (e) {
          console.error('[useSerialScanner] port.open failed:', e)
          // InvalidStateError — порт уже открыт другим контекстом. Если readable есть — продолжаем.
          if (port.readable === null) {
            setError(humanizeSerialOpenError(e))
            setConnected(false)
            return
          }
        }
      }
      portRef.current = port
      setConnected(true)
      setError(null)
      void startReader(port)
    },
    [startReader],
  )

  const requestConnect = useCallback(async () => {
    if (!supported) {
      setError('Браузер не поддерживает Web Serial API')
      return
    }
    try {
      const port = await navigator.serial.requestPort()
      await closePort()
      await openAndRead(port)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      // NotFoundError — пользователь закрыл диалог выбора порта, не показываем как ошибку.
      if (!msg.toLowerCase().includes('no port selected') && !msg.includes('NotFoundError')) {
        setError(humanizeSerialOpenError(e))
      }
    }
  }, [supported, closePort, openAndRead])

  const disconnect = useCallback(async () => {
    await closePort()
  }, [closePort])

  // Авто-подключение к ранее авторизованному порту при mount/enabled
  useEffect(() => {
    if (!enabled) return
    if (!supported) {
      setError('Браузер не поддерживает Web Serial API. Используйте Chrome или Edge.')
      return
    }

    let cancelled = false

    void (async () => {
      try {
        const ports = await navigator.serial.getPorts()
        if (cancelled) return
        if (ports.length === 0) {
          setError('Нет авторизованного COM-порта. Откройте «Настройки» и нажмите «Подключить COM-порт».')
          return
        }
        await openAndRead(ports[0])
      } catch (e) {
        if (cancelled) return
        const msg = e instanceof Error ? e.message : String(e)
        console.error('[useSerialScanner] getPorts failed:', e)
        setError(`Не удалось получить список COM-портов: ${msg}`)
      }
    })()

    const onDisconnect = (ev: Event & { target: SerialPort }) => {
      if (ev.target === portRef.current) {
        void closePort()
        setError('COM-порт отключён')
      }
    }
    navigator.serial.addEventListener('disconnect', onDisconnect)

    return () => {
      cancelled = true
      navigator.serial.removeEventListener('disconnect', onDisconnect)
      void closePort()
    }
  }, [enabled, supported, openAndRead, closePort])

  return { supported, connected, error, requestConnect, disconnect }
}
