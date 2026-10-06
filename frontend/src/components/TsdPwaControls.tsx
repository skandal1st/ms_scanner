import { useEffect, useState } from 'react'

interface InstallEvent extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

export function useTsdOnline() {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const update = () => setOnline(navigator.onLine)
    window.addEventListener('online', update)
    window.addEventListener('offline', update)
    return () => {
      window.removeEventListener('online', update)
      window.removeEventListener('offline', update)
    }
  }, [])
  return online
}

export function TsdConnection() {
  const online = useTsdOnline()
  return <span className={`tsd-online ${online ? '' : 'tsd-online--offline'}`}><i />{online ? 'Онлайн' : 'Нет сети'}</span>
}

export function TsdPwaControls() {
  const online = useTsdOnline()
  // Android's standard WebView user agent includes "; wv". Existing APKs use it.
  const nativeApp = /Android/i.test(navigator.userAgent) && /;\s*wv\b/i.test(navigator.userAgent)
  const [prompt, setPrompt] = useState<InstallEvent | null>(null)
  const [installed, setInstalled] = useState(() => window.matchMedia('(display-mode: standalone)').matches)
  const [help, setHelp] = useState(false)
  useEffect(() => {
    if (nativeApp) return
    const install = (event: Event) => { event.preventDefault(); setPrompt(event as InstallEvent) }
    const done = () => { setInstalled(true); setPrompt(null) }
    window.addEventListener('beforeinstallprompt', install)
    window.addEventListener('appinstalled', done)
    return () => {
      window.removeEventListener('beforeinstallprompt', install)
      window.removeEventListener('appinstalled', done)
    }
  }, [nativeApp])
  const install = async () => {
    if (!prompt) { setHelp((value) => !value); return }
    try { await prompt.prompt(); await prompt.userChoice } finally { setPrompt(null) }
  }
  return <aside className="tsd-pwa" aria-label="Мобильное приложение">
    {!nativeApp && !installed && <button className="tsd-button" type="button" onClick={install}>Установить на ТСД</button>}
    {!nativeApp && help && <p>В Chrome откройте меню ⋮ → «Установить приложение» или «Добавить на главный экран». Для установки нужен HTTPS. После установки откройте приложение и отсканируйте свежий QR подключения из настроек Скандаты.</p>}
    {!online && <p role="alert" className="tsd-alert tsd-alert--warn">Нет сети. Сканирование остановлено. Марки не отправляются и не сохраняются в очередь. Подключитесь к Wi-Fi и повторите скан.</p>}
  </aside>
}
