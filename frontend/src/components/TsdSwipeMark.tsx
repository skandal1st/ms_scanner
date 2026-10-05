import { useRef, useState, type ReactNode, type PointerEvent } from 'react'
import { Icon } from './Icon'

const ACTION_WIDTH = 64

export function TsdSwipeMark({ children, selected, open, className, disabled, onSelect, onReveal, onDelete, deleteLabel }: {
  children: ReactNode; selected: boolean; open: boolean; className: string; disabled: boolean
  onSelect: () => void; onReveal: (open: boolean) => void; onDelete: () => void; deleteLabel: string
}) {
  const gesture = useRef<{ id: number; x: number; y: number; start: number; moved: boolean } | null>(null)
  const suppressClick = useRef(false)
  const [offset, setOffset] = useState<number | null>(null)
  const end = (event: PointerEvent<HTMLButtonElement>, cancelled = false) => {
    const current = gesture.current
    if (!current || current.id !== event.pointerId) return
    if (current.moved) {
      suppressClick.current = true
      onReveal(cancelled ? open : current.start + event.clientX - current.x < -ACTION_WIDTH / 2)
    }
    gesture.current = null
    setOffset(null)
  }
  return <div className="tsd-swipe-mark">
    <button type="button" className="tsd-swipe-mark__delete" aria-label={deleteLabel} title={deleteLabel}
      disabled={disabled || !open} tabIndex={open ? 0 : -1} aria-hidden={!open}
      onClick={onDelete}><Icon name="trash" size={24} /></button>
    <button type="button" className={className} aria-pressed={selected} disabled={disabled}
      style={{ transform: `translateX(${offset ?? (open ? -ACTION_WIDTH : 0)}px)`, transition: offset === null ? undefined : 'none' }}
      onPointerDown={event => {
        if (disabled || !event.isPrimary || event.button !== 0) return
        suppressClick.current = false
        gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY, start: open ? -ACTION_WIDTH : 0, moved: false }
      }}
      onPointerMove={event => {
        const current = gesture.current
        if (!current || current.id !== event.pointerId) return
        const dx = event.clientX - current.x, dy = event.clientY - current.y
        if (!current.moved) {
          if (Math.abs(dy) > 12 && Math.abs(dy) >= Math.abs(dx)) { gesture.current = null; return }
          if (Math.abs(dx) < 12 || Math.abs(dx) < Math.abs(dy) * 1.3) return
          current.moved = true
          event.currentTarget.setPointerCapture(event.pointerId)
        }
        setOffset(Math.max(-ACTION_WIDTH, Math.min(0, current.start + dx)))
      }}
      onPointerUp={event => end(event)} onPointerCancel={event => end(event, true)}
      onClick={() => { if (suppressClick.current) { suppressClick.current = false; return }; onSelect() }}>
      {children}
    </button>
  </div>
}
