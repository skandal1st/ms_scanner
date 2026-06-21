import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Ширины столбцов с перетаскиванием границ и сохранением в localStorage.
 *
 * Возвращает текущие ширины (px) и обработчик начала перетаскивания границы
 * столбца. Значения переживают перезагрузку страницы (ключ storageKey).
 */
export function useColumnWidths(
  storageKey: string,
  defaults: Record<string, number>,
) {
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) return { ...defaults, ...JSON.parse(raw) }
    } catch {
      /* битый JSON — игнорируем, берём дефолты */
    }
    return defaults
  })

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(widths))
    } catch {
      /* приватный режим / квота — не критично */
    }
  }, [storageKey, widths])

  const drag = useRef<{ key: string; startX: number; startW: number; min: number } | null>(null)

  const startResize = useCallback(
    (key: string, min: number) => (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      drag.current = {
        key,
        startX: e.clientX,
        startW: widths[key] ?? defaults[key] ?? 120,
        min,
      }

      const onMove = (ev: MouseEvent) => {
        const d = drag.current
        if (!d) return
        const next = Math.max(d.min, d.startW + (ev.clientX - d.startX))
        setWidths((w) => ({ ...w, [d.key]: next }))
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
    [widths, defaults],
  )

  return { widths, startResize }
}
