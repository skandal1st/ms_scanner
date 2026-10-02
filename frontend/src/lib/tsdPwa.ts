/// <reference types="vite/client" />
// Only the TSD route advertises and registers the mobile application.
if (window.location.pathname === '/tsd') {
  const manifest = document.createElement('link')
  manifest.rel = 'manifest'
  manifest.href = '/tsd.webmanifest'
  document.head.append(manifest)
  const theme = document.createElement('meta')
  theme.name = 'theme-color'
  theme.content = '#17233d'
  document.head.append(theme)
  const icon = document.createElement('link')
  icon.rel = 'apple-touch-icon'
  icon.href = '/tsd-icon-192.png'
  document.head.append(icon)
  if (import.meta.env.PROD && 'serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/tsd-sw.js', { scope: '/tsd', updateViaCache: 'none' })
        .catch(() => { /* Installation is optional; online scanning still works. */ })
    }, { once: true })
  }
}
