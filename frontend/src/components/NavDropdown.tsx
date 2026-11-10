import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { Icon } from './Icon'

export function NavDropdown({ label, active, className, children }: {
  label: string
  active?: boolean
  className: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const container = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panelId = useId()
  const location = useLocation()

  useEffect(() => setOpen(false), [location.key])
  useEffect(() => {
    if (!open) return
    const dismiss = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    return () => document.removeEventListener('pointerdown', dismiss)
  }, [open])

  return <div ref={container} className={`app-nav__dropdown ${className}`}
    onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
    }}
    onKeyDown={event => {
      if (open && event.key === 'Escape') {
        setOpen(false)
        trigger.current?.focus()
      }
    }}>
    <button type="button" ref={trigger} aria-expanded={open} aria-controls={panelId}
      className={active ? 'active' : undefined} title={label} onClick={() => setOpen(!open)}>
      <span>{label}</span><Icon name="chevron" size={13} />
    </button>
    <div id={panelId} className="app-nav__panel" hidden={!open}
      onClick={event => { if ((event.target as Element).closest('a')) setOpen(false) }}>
      {children}
    </div>
  </div>
}
