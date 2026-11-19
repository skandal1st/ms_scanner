// Буфер строк для Web Serial: аккумулирует входящие фрагменты, режет по CR/LF/CRLF,
// отдаёт массив целых линий. Хвост без терминатора остаётся в буфере.
// FNC1 (0x1D) — часть GS1 DataMatrix, НЕ терминатор: сохраняется внутри линии как есть.

// Нормализация разделителя GS (Group Separator). По стандарту GS1 это 0x1D, но сканеры
// часто настроены слать его подменным байтом — типичная настройка под 1С — 0xF8
// (после latin1-декода это U+00F8). Приводим к каноничному 0x1D, который ждёт бэкенд
// (cis_string_for_moysklad_api оставляет только 0x1D и печатные ASCII, остальное режет).
const GS = '\x1d'
export function normalizeGs(s: string): string {
  return s.replace(/[ø]/g, GS)
}

export class SerialLineBuffer {
  private pending: number[] = []
  private decoder = new TextDecoder('latin1')

  feed(chunk: Uint8Array) {
    const frames: { line: string; rawHex?: string }[] = []
    for (const byte of chunk) {
      if (byte !== 13 && byte !== 10) {
        this.pending.push(byte)
        continue
      }
      if (!this.pending.length) continue
      // Capture the bytes of this frame before text decoding or GS substitution.
      const rawHex = this.pending.length <= 4096
        ? this.pending.map(value => value.toString(16).padStart(2, '0')).join('')
        : undefined
      frames.push({ line: this.decoder.decode(Uint8Array.from(this.pending)), rawHex })
      this.pending = []
    }
    return frames
  }
}
