import { useEffect, useMemo, useState } from 'react'
import { createTsdSound } from '../lib/tsdSound'

export function useTsdSound() {
  const sound = useMemo(() => createTsdSound(), [])
  const [enabled, setEnabled] = useState(() => localStorage.getItem('tsd_sound_enabled') !== '0')
  const [ready, setReady] = useState(false)
  useEffect(() => {
    if (!enabled) return
    let active = true
    const unlock = (event: Event) => {
      if (event.target instanceof Element && event.target.closest('[data-tsd-sound]')) return
      void sound.activate().then((value) => { if (active) setReady(value) })
    }
    window.addEventListener('pointerdown', unlock, true)
    window.addEventListener('keydown', unlock, true)
    return () => {
      active = false
      window.removeEventListener('pointerdown', unlock, true)
      window.removeEventListener('keydown', unlock, true)
    }
  }, [sound, enabled])
  useEffect(() => () => sound.close(), [sound])
  return {
    active: enabled && ready,
    play: (kind: 'ok' | 'error') => { if (enabled) sound.play(kind) },
    async toggle() {
      if (enabled && ready) {
        setEnabled(false)
        localStorage.setItem('tsd_sound_enabled', '0')
      } else {
        setEnabled(true)
        localStorage.setItem('tsd_sound_enabled', '1')
        const activated = await sound.activate()
        setReady(activated)
        if (activated) sound.play('ok')
      }
    },
  }
}
