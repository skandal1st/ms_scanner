/** Web Audio is activated by a user gesture, never during module import. */
export function createTsdSound() {
  let context: AudioContext | null = null
  return {
    async activate(): Promise<boolean> {
      try {
        const Constructor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
        if (!Constructor) return false
        if (!context || context.state === 'closed') context = new Constructor()
        if (context.state === 'suspended') await context.resume()
        return context.state === 'running'
      } catch { return false }
    },
    play(kind: 'ok' | 'error') {
      if (!context || context.state !== 'running') return
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
    close() { if (context) void context.close().catch(() => {}) },
  }
}
