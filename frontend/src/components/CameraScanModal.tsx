import { useEffect, useRef, useState, useCallback } from 'react'
import { BrowserDatamatrixCodeReader } from '@zxing/browser'
import { normalizeScannerInput } from '../lib/scannerLayout'

const DEBOUNCE_MS = 650

const DM_FORMAT_CANDIDATES = ['data_matrix', 'gs1_data_matrix', 'gs1datamatrix'] as const

/** MDN BarcodeDetector — в tsconfig без lib «новых» DOM-типов. */
interface BarcodeDetectorInstance {
  detect(image: HTMLVideoElement): Promise<Array<{ rawValue?: string }>>
}
interface BarcodeDetectorGlobal {
  new (options: { formats: string[] }): BarcodeDetectorInstance
  getSupportedFormats(): Promise<readonly string[]>
}

function getBarcodeDetectorClass(): BarcodeDetectorGlobal | undefined {
  return (globalThis as unknown as { BarcodeDetector?: BarcodeDetectorGlobal }).BarcodeDetector
}

async function pickBarcodeDetectorFormatsAsync(): Promise<string[] | null> {
  const BarcodeDetector = getBarcodeDetectorClass()
  if (typeof window === 'undefined' || !BarcodeDetector) {
    return null
  }
  try {
    const supported = await BarcodeDetector.getSupportedFormats()
    const picked = DM_FORMAT_CANDIDATES.filter((f) =>
      (supported as readonly string[]).includes(f),
    )
    return picked.length > 0 ? [...picked] : null
  } catch {
    return null
  }
}

type Props = {
  open: boolean
  onClose: () => void
  onCode: (code: string) => void | Promise<void>
}

export function CameraScanModal({ open, onClose, onCode }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const rafRef = useRef<number>(0)
  const zxingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastFireRef = useRef(0)
  const busyRef = useRef(false)
  const detectorRef = useRef<BarcodeDetectorInstance | null>(null)
  const dmReaderRef = useRef<BrowserDatamatrixCodeReader | null>(null)
  const onCodeRef = useRef(onCode)
  const onCloseRef = useRef(onClose)
  const [error, setError] = useState<string | null>(null)

  onCodeRef.current = onCode
  onCloseRef.current = onClose

  const stopStream = useCallback(() => {
    const s = streamRef.current
    streamRef.current = null
    if (s) {
      for (const t of s.getTracks()) {
        t.stop()
      }
    }
    const v = videoRef.current
    if (v) {
      v.srcObject = null
    }
  }, [])

  const tryDecodeOnce = useCallback(async () => {
    const video = videoRef.current
    if (!video || video.readyState < 2 || busyRef.current) return

    const now = Date.now()
    if (now - lastFireRef.current < DEBOUNCE_MS) return

    const detector = detectorRef.current
    if (detector) {
      try {
        const codes = await detector.detect(video)
        if (codes && codes.length > 0) {
          const raw = codes[0].rawValue?.trim()
          if (raw) {
            const code = normalizeScannerInput(raw).trim()
            if (code) {
              busyRef.current = true
              lastFireRef.current = Date.now()
              try {
                await onCodeRef.current(code)
                onCloseRef.current()
              } finally {
                busyRef.current = false
              }
            }
          }
        }
      } catch {
        /* пустой кадр / отказ детектора — норма */
      }
      return
    }

    const reader = dmReaderRef.current
    if (!reader) return
    try {
      const result = reader.decode(video)
      const raw = result.getText()?.trim()
      if (raw) {
        const code = normalizeScannerInput(raw).trim()
        if (code) {
          busyRef.current = true
          lastFireRef.current = Date.now()
          try {
            await onCodeRef.current(code)
            onCloseRef.current()
          } finally {
            busyRef.current = false
          }
        }
      }
    } catch {
      /* NotFound и пр. — ждём следующий кадр */
    }
  }, [])

  useEffect(() => {
    if (!open) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
      if (zxingTimerRef.current) {
        clearInterval(zxingTimerRef.current)
        zxingTimerRef.current = null
      }
      detectorRef.current = null
      dmReaderRef.current = null
      stopStream()
      setError(null)
      return
    }

    if (!window.isSecureContext) {
      setError('Камера доступна только по HTTPS. Откройте приложение по защищённому адресу.')
      return
    }

    let cancelled = false

    void (async () => {
      setError(null)
      const video = videoRef.current
      if (!video) return

      const formats = await pickBarcodeDetectorFormatsAsync()
      if (cancelled) return

      const BarcodeDetector = getBarcodeDetectorClass()
      if (formats && formats.length > 0 && BarcodeDetector) {
        try {
          detectorRef.current = new BarcodeDetector({ formats })
        } catch {
          detectorRef.current = null
        }
      }

      if (!detectorRef.current) {
        dmReaderRef.current = new BrowserDatamatrixCodeReader()
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false,
        })
        if (cancelled) {
          for (const t of stream.getTracks()) t.stop()
          return
        }
        streamRef.current = stream
        video.srcObject = stream
        video.muted = true
        video.setAttribute('playsinline', 'true')
        await video.play()
      } catch {
        if (!cancelled) {
          setError(
            'Не удалось включить камеру. Разрешите доступ в настройках браузера или попробуйте сканер/USB.',
          )
        }
        return
      }

      if (cancelled) return

      if (detectorRef.current) {
        const tick = () => {
          if (cancelled) return
          void tryDecodeOnce()
          rafRef.current = requestAnimationFrame(tick)
        }
        rafRef.current = requestAnimationFrame(tick)
      } else {
        zxingTimerRef.current = setInterval(() => void tryDecodeOnce(), 180)
      }
    })()

    return () => {
      cancelled = true
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
      if (zxingTimerRef.current) {
        clearInterval(zxingTimerRef.current)
        zxingTimerRef.current = null
      }
      detectorRef.current = null
      dmReaderRef.current = null
      stopStream()
    }
  }, [open, stopStream, tryDecodeOnce])

  if (!open) return null

  return (
    <div className="camera-scan-overlay" role="dialog" aria-modal="true" aria-label="Сканирование камерой">
      <div className="camera-scan-panel">
        <div className="camera-scan-head">
          <span className="camera-scan-title">Камера</span>
          <button type="button" className="button button--link camera-scan-close" onClick={onClose}>
            Закрыть
          </button>
        </div>
        <video ref={videoRef} className="camera-scan-video" playsInline muted />
        {error ? (
          <p className="camera-scan-error">{error}</p>
        ) : (
          <p className="camera-scan-hint">
            Наведите на Data Matrix марки. При хорошем освещении распознавание быстрее.
          </p>
        )}
      </div>
    </div>
  )
}
