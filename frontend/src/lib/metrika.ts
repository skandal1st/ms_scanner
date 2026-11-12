import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

const counter = 114221981
const tagUrl = `https://mc.yandex.ru/metrika/tag.js?id=${counter}`
type Ym = ((...args: unknown[]) => void) & { a?: unknown[][]; l?: number }
declare global {
  interface Window { ym?: Ym }
}

let lastPage = ''

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
        defer: true, ssr: true, clickmap: true, ecommerce: 'dataLayer',
        accurateTrackBounce: true, trackLinks: true, url: page, referrer: referrerOrigin(),
      })
    }
    window.ym?.(counter, 'hit', page, { referer: lastPage || referrerOrigin() })
    lastPage = page
  }, [pathname])
  return null
}
