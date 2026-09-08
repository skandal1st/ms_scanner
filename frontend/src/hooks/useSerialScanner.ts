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

// Имя эксклюзивного Web Lock — гейт «одна активная вкладка Скандаты держит сканер».
const SCANNER_LOCK = 'scandata-serial-scanner'
// Пауза между фоновыми попытками открыть занятый порт.
const REOPEN_DELAY_MS = 1500

// Ошибка «порт занят» (эксклюзив держит другой процесс/вкладка). Chrome при занятом порте:
// DOMException NetworkError "Failed to open serial port."
export function isPortBusyError(e: unknown): boolean {
  const name = e instanceof DOMException ? e.name : ''
  const msg = e instanceof Error ? e.message : String(e)
  const low = msg.toLowerCase()
  return (
    name === 'NetworkError' ||
    low.includes('failed to open serial port') ||
    low.includes('already open') ||
    low.includes('access is denied') ||
    low.includes('access denied')
  )
}

// Понятный русский текст ошибки открытия порта для кладовщика.
export function humanizeSerialOpenError(e: unknown): string {
  if (isPortBusyError(e)) {
    return 'COM-порт занят другой программой или вкладкой. Закройте другие окна приложения и программы, использующие сканер, затем подключитесь снова.'
  }
  const msg = e instanceof Error ? e.message : String(e)
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
  // Промис цикла чтения: закрытие порта ДОЛЖНО дождаться его завершения, иначе lock
  // на port.readable останется висеть и port.close() либо повиснет, либо следующий
  // port.open() упадёт с «порт занят» (лечилось только Ctrl+Shift+R).
  const loopRef = useRef<Promise<void> | null>(null)
  const aliveRef = useRef(false)
  // Намерение быть подключённым: фоновый авто-повтор open работает, пока true.
  const wantRef = useRef(false)
  // Таймер фонового повтора открытия занятого порта.
  const retryTimerRef = useRef<number | null>(null)
  // Release-функция Web Lock «одна вкладка»; null — лок не держим.
  const releaseLockRef = useRef<(() => void) | null>(null)
  // Актуальная реализация openAndRead для вызова из таймера без циклов зависимостей.
  const openAndReadRef = useRef<(port: SerialPort) => Promise<void>>(async () => {})
  // onCode передаётся через ref, чтобы изменение callback не перезапускало reader-loop.
  const onCodeRef = useRef(onCode)
  useEffect(() => {
    onCodeRef.current = onCode
  }, [onCode])

  const clearRetry = useCallback(() => {
    if (retryTimerRef.current != null) {
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }, [])

  const stopReader = useCallback(async () => {
    aliveRef.current = false
    const reader = readerRef.current
    readerRef.current = null
    if (reader) {
      // cancel() разбудит зависший read() → цикл выйдет и сам снимет lock в finally.
      try {
        await reader.cancel()
      } catch {
        /* ignore */
      }
    }
    // Ждём завершения цикла — только после этого lock на readable гарантированно снят.
    const loop = loopRef.current
    loopRef.current = null
    if (loop) {
      try {
        await loop
      } catch {
        /* ignore */
      }
    }
  }, [])

  // Гейт «одна вкладка»: отдать эксклюзив на сканер другим вкладкам Скандаты.
  const releaseTabLock = useCallback(() => {
    const release = releaseLockRef.current
    releaseLockRef.current = null
    if (release) release()
  }, [])

  // Захватить эксклюзив на сканер среди вкладок. Если его держит другая вкладка — наш
  // request встаёт в очередь Web Locks и получит лок автоматически, как только та вкладка
  // отключит порт (без ручного повтора). Старый браузер без Web Locks — гейт пропускаем.
  const acquireTabLock = useCallback((): Promise<boolean> => {
    const nav = navigator as Navigator & { locks?: LockManager }
    if (!nav.locks) return Promise.resolve(true)
    if (releaseLockRef.current) return Promise.resolve(true)
    return new Promise<boolean>((resolveAcquired) => {
      const held = new Promise<void>((release) => {
        releaseLockRef.current = release
      })
      nav.locks!
        .request(SCANNER_LOCK, async () => {
          resolveAcquired(true)
          await held // держим лок, пока не вызовем releaseTabLock()
        })
        .catch(() => {
          releaseLockRef.current = null
          resolveAcquired(false)
        })
    })
  }, [])

  const closePort = useCallback(async () => {
    // Больше не пытаемся переоткрываться и снимаем фоновый повтор.
    wantRef.current = false
    clearRetry()
    // Обнуляем portRef СРАЗУ: слушатель `connect` во время закрытия не должен принять
    // закрываемый порт за «живой» и пропустить авто-переоткрытие после ре-энумерации.
    const port = portRef.current
    portRef.current = null
    await stopReader()
    if (port) {
      try {
        await port.close()
      } catch {
        /* ignore — порт мог быть уже отключён физически */
      }
    }
    releaseTabLock() // отдаём сканер другим вкладкам
    setConnected(false)
  }, [stopReader, clearRetry, releaseTabLock])

  const startReader = useCallback((port: SerialPort) => {
    if (!port.readable) {
      setError('COM-порт не доступен для чтения')
      return
    }
    const reader = port.readable.getReader()
    readerRef.current = reader
    aliveRef.current = true
    // Цикл чтения живёт в отдельном промисе (loopRef): закрытие порта его дожидается,
    // а lock на readable снимается ровно один раз — в finally этого цикла.
    loopRef.current = (async () => {
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
    })()
  }, [])

  const openAndRead = useCallback(
    async (port: SerialPort) => {
      // Если порт уже открыт (например, мы же его и держим) — port.readable не null.
      const alreadyOpen = port.readable !== null
      if (!alreadyOpen) {
        try {
          await port.open(PORT_OPTIONS)
        } catch (e) {
          // readable != null → порт всё же открыт другим контекстом, продолжаем чтение.
          if (port.readable === null) {
            // Транзиентный «занят» после close/ре-энумерации USB — ОС могла не успеть
            // освободить хендл. Дадим паузу и попробуем ещё раз.
            if (isPortBusyError(e)) {
              await new Promise((r) => setTimeout(r, 500))
            }
            try {
              await port.open(PORT_OPTIONS)
            } catch (e2) {
              if (port.readable === null) {
                setConnected(false)
                if (isPortBusyError(e2) && wantRef.current) {
                  // Порт держит внешняя программа (1С/утилита сканера). Отобрать эксклюзив
                  // нельзя, но и сдаваться не нужно: тихо пробуем снова — подключимся сами,
                  // как только программа освободит порт. Без Ctrl+Shift+R.
                  setError(
                    'COM-порт занят другой программой (например, 1С). Подключусь автоматически, как только он освободится…',
                  )
                  clearRetry()
                  retryTimerRef.current = window.setTimeout(() => {
                    if (wantRef.current) void openAndReadRef.current(port)
                  }, REOPEN_DELAY_MS)
                } else {
                  console.error('[useSerialScanner] port.open failed:', e2)
                  setError(humanizeSerialOpenError(e2))
                }
                return
              }
            }
          }
        }
      }
      portRef.current = port
      clearRetry()
      setConnected(true)
      setError(null)
      startReader(port)
    },
    [startReader, clearRetry],
  )
  useEffect(() => {
    openAndReadRef.current = openAndRead
  }, [openAndRead])

  // Полный вход в подключение: сперва гейт одной вкладки, затем открытие физического порта.
  const connectPort = useCallback(
    async (port: SerialPort) => {
      wantRef.current = true
      const nav = navigator as Navigator & { locks?: LockManager }
      if (nav.locks && !releaseLockRef.current) {
        // Если сканер уже держит другая вкладка — скажем об этом; наш lock встанет в очередь.
        try {
          const state = await nav.locks.query()
          const busyTab = state.held?.some((l) => l.name === SCANNER_LOCK)
          if (busyTab) {
            setError(
              'Сканер занят другой вкладкой Скандаты. Подключусь автоматически, как только она освободит порт…',
            )
          }
        } catch {
          /* ignore */
        }
      }
      const ok = await acquireTabLock()
      if (!ok || !wantRef.current) return
      await openAndRead(port)
    },
    [acquireTabLock, openAndRead],
  )

  const requestConnect = useCallback(async () => {
    if (!supported) {
      setError('Браузер не поддерживает Web Serial API')
      return
    }
    try {
      const port = await navigator.serial.requestPort()
      await closePort()
      await connectPort(port)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      // NotFoundError — пользователь закрыл диалог выбора порта, не показываем как ошибку.
      if (!msg.toLowerCase().includes('no port selected') && !msg.includes('NotFoundError')) {
        setError(humanizeSerialOpenError(e))
      }
    }
  }, [supported, closePort, connectPort])

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
        await connectPort(ports[0])
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
        setError('COM-порт отключён. Ожидаю переподключения сканера…')
      }
    }
    navigator.serial.addEventListener('disconnect', onDisconnect)

    // USB-сканеры при ре-энумерации порта шлют `connect` заново. Если своего порта уже
    // не держим — открываем вернувшийся автоматически, без Ctrl+Shift+R.
    const onConnect = (ev: Event & { target: SerialPort }) => {
      if (cancelled || portRef.current) return
      void connectPort(ev.target)
    }
    navigator.serial.addEventListener('connect', onConnect)

    return () => {
      cancelled = true
      navigator.serial.removeEventListener('disconnect', onDisconnect)
      navigator.serial.removeEventListener('connect', onConnect)
      void closePort()
    }
  }, [enabled, supported, connectPort, closePort])

  return { supported, connected, error, requestConnect, disconnect }
}
