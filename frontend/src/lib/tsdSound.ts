/** Decode once and restart the clip on repeated conflicts instead of overlapping it. */
export function createConflictSound() {
  let loaded: Promise<AudioBuffer> | null = null
  let source: AudioBufferSourceNode | null = null
  let sequence = 0
  const prepare = (context: AudioContext) => {
    if (!loaded) {
      loaded = Promise.resolve().then(() => fetch('/sounds/used-in-other-document.mp3'))
        .then(response => {
          if (!response.ok) throw new Error('Не удалось загрузить звук')
          return response.arrayBuffer()
        })
        .then(data => context.decodeAudioData(data))
        .catch(error => { loaded = null; throw error })
    }
    return loaded
  }
  const stop = () => {
    sequence++
    if (source) {
      try { source.stop() } catch { /* Already ended. */ }
      source.disconnect()
      source = null
    }
  }
  return {
    prepare,
    stop,
    play(context: AudioContext, fallback: () => void) {
      stop()
      const current = sequence
      void prepare(context).then(buffer => {
        if (current !== sequence || context.state !== 'running') return
        const clip = context.createBufferSource()
        clip.buffer = buffer
        clip.connect(context.destination)
        clip.onended = () => { clip.disconnect(); if (source === clip) source = null }
        source = clip
        clip.start()
      }).catch(() => { if (current === sequence && context.state === 'running') fallback() })
    },
  }
}

/** Web Audio is activated by a user gesture, never during module import. */
export function createTsdSound() {
  let context: AudioContext | null = null
  const conflict = createConflictSound()
  return {
    async activate(): Promise<boolean> {
      try {
        const Constructor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
        if (!Constructor) return false
        if (!context || context.state === 'closed') context = new Constructor()
        if (context.state === 'suspended') await context.resume()
        void conflict.prepare(context).catch(() => {})
        return context.state === 'running'
      } catch { return false }
    },
    play(kind: 'ok' | 'error' | 'used_in_other_doc') {
      if (!context || context.state !== 'running') return
      if (kind === 'used_in_other_doc') {
        conflict.play(context, () => this.play('error'))
        return
      }
      try {
        const now = context.currentTime
        for (const offset of kind === 'error' ? [0, 0.2] : [0]) {
          const oscillator = context.createOscillator()
          const gain = context.createGain()
          oscillator.connect(gain)
          gain.connect(context.destination)
          oscillator.frequency.value = kind === 'error' ? 220 : 880
          const start = now + offset
          gain.gain.setValueAtTime(0.22, start)
          gain.gain.exponentialRampToValueAtTime(0.001, start + 0.14)
          oscillator.onended = () => { oscillator.disconnect(); gain.disconnect() }
          oscillator.start(start)
          oscillator.stop(start + 0.14)
        }
      } catch { /* Audio failure must never interrupt scanning. */ }
    },
    stop() { conflict.stop() },
    close() { conflict.stop(); if (context) void context.close().catch(() => {}) },
  }
}
