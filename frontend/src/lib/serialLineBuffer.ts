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
  private buf = ''

  feed(chunk: string): string[] {
    this.buf += chunk
    const parts = this.buf.split(/\r\n|\r|\n/)
    // Последний элемент — хвост без терминатора, оставляем в буфере.
    this.buf = parts.pop() ?? ''
    return parts.filter((line) => line.length > 0)
  }

  reset(): void {
    this.buf = ''
  }
}
