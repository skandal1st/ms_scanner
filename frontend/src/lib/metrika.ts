import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

const counter = 114221981
const tagUrl = `https://mc.yandex.ru/metrika/tag.js?id=${counter}`
type Ym = ((...args: unknown[]) => void) & { a?: unknown[][]; l?: number }
declare global {
  interface Window { ym?: Ym }
}

let lastPage = ''

type Goal = 'solution_opened' | 'shipment_selected' | 'first_mark_scanned' | 'acceptance_started'
const reached = new Set<Goal>()
const pending = new Set<Goal>()

/** One milestone per tab. No business identifiers are stored or sent. */
export function reachGoal(goal: Goal) {
  if (window.location.hostname !== 'skandata.ru' || reached.has(goal)) return
  if (!lastPage || !window.ym) { pending.add(goal); return }
  const key = 'skandata.goal.' + goal
  try {
    if (sessionStorage.getItem(key)) return
    sessionStorage.setItem(key, '1')
  } catch { /* Embedded browsers may restrict storage. */ }
  reached.add(goal)
  try { window.ym(counter, 'reachGoal', goal) } catch { /* Keep warehouse operations independent of analytics. */ }
}

export function recordMarkScan(scan: { status: string; duplicate?: boolean | null; is_barcode?: boolean | null }) {
  if (!scan.duplicate && !scan.is_barcode && ['scanned', 'valid', 'overflow'].includes(scan.status)) {
    reachGoal('first_mark_scanned')
  }
}

function referrerOrigin() {
  try { return new URL(document.referrer).origin } catch { return '' }
}

export function Metrika() {
  const { pathname } = useLocation()
  useEffect(() => {
    if (window.location.hostname !== 'skandata.ru') return
    // Launch, OAuth and TSD pairing parameters are credentials. Report only the route.
    const page = window.location.origin + pathname
    if (page === lastPage) return
    if (!lastPage) {
      const queue: Ym = (...args) => { (queue.a ??= []).push(args) }
      queue.l = Date.now()
      window.ym ??= queue
      if (!Array.from(document.scripts).some(script => script.src === tagUrl)) {
        const script = document.createElement('script')
        script.async = true
        script.src = tagUrl
        script.referrerPolicy = 'no-referrer'
        document.head.append(script)
      }
      window.ym(counter, 'init', {
        ssr: true, clickmap: true, ecommerce: 'dataLayer',
        accurateTrackBounce: true, trackLinks: true, url: page, referrer: referrerOrigin(),
      })
    } else {
      window.ym?.(counter, 'hit', page, { referer: lastPage })
    }
    lastPage = page
    pending.forEach(reachGoal)
    pending.clear()
  }, [pathname])
  return null
}
