import { useCallback, useEffect, useRef, useState } from 'react'

const clamp = (n: number, min: number, max: number) =>
  Math.min(Math.max(n, min), max)

/**
 * Ширина панели (px) с перетаскиванием границы мышью и сохранением в localStorage.
 *
 * Возвращает текущую ширину и обработчик начала перетаскивания (вешать на
 * вертикальный разделитель). Значение переживает перезагрузку (ключ storageKey).
 * `reserveRight` — сколько px гарантированно оставить правой части, чтобы её
 * нельзя было схлопнуть перетаскиванием (динамический максимум по ширине окна).
 */
export function useResizableWidth(
  storageKey: string,
  defaultWidth: number,
  opts: { min: number; max: number; reserveRight?: number },
) {
  const { min, max, reserveRight = 360 } = opts

  const [width, setWidth] = useState<number>(() => {
    let initial = defaultWidth
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw != null) {
        const n = Number(JSON.parse(raw))
        if (Number.isFinite(n)) initial = n
      }
    } catch {
      /* битый JSON — берём дефолт */
    }
    return clamp(initial, min, max)
  })

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(width))
    } catch {
      /* приватный режим / квота — не критично */
    }
  }, [storageKey, width])

  const drag = useRef<{ startX: number; startW: number } | null>(null)

  const startResize = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      drag.current = { startX: e.clientX, startW: width }

      const onMove = (ev: MouseEvent) => {
        const d = drag.current
        if (!d) return
        const hardMax = Math.min(max, window.innerWidth - reserveRight)
        setWidth(clamp(d.startW + (ev.clientX - d.startX), min, hardMax))
      }
      const onUp = () => {
        drag.current = null
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }

      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
    },
    [width, min, max, reserveRight],
  )

  return { width, startResize }
}
