import { useCallback, useEffect, useRef, useState } from 'react'

const clamp = (n: number, min: number, max: number) =>
  Math.min(Math.max(n, min), max)

/**
 * Высота панели (px) с перетаскиванием нижней границы мышью и сохранением в
 * localStorage. Зеркалит {@link useResizableWidth}, только по вертикали.
 *
 * Возвращает текущую высоту и обработчик начала перетаскивания (вешать на
 * горизонтальный разделитель). Значение переживает перезагрузку (ключ storageKey).
 * `reserveBottom` — сколько px гарантированно оставить нижнему блоку (таблице
 * кодов), чтобы его нельзя было схлопнуть перетаскиванием (динамический максимум
 * по высоте окна).
 */
export function useResizableHeight(
  storageKey: string,
  defaultHeight: number,
  opts: { min: number; max: number; reserveBottom?: number },
) {
  const { min, max, reserveBottom = 260 } = opts

  const [height, setHeight] = useState<number>(() => {
    let initial = defaultHeight
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
      localStorage.setItem(storageKey, JSON.stringify(height))
    } catch {
      /* приватный режим / квота — не критично */
    }
  }, [storageKey, height])

  const drag = useRef<{ startY: number; startH: number } | null>(null)

  const startResize = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      drag.current = { startY: e.clientY, startH: height }

      const onMove = (ev: MouseEvent) => {
        const d = drag.current
        if (!d) return
        const hardMax = Math.min(max, window.innerHeight - reserveBottom)
        setHeight(clamp(d.startH + (ev.clientY - d.startY), min, hardMax))
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
      document.body.style.cursor = 'row-resize'
      document.body.style.userSelect = 'none'
    },
    [height, min, max, reserveBottom],
  )

  return { height, startResize }
}
