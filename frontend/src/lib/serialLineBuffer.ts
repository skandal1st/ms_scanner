// Буфер строк для Web Serial: аккумулирует входящие фрагменты, режет по CR/LF/CRLF,
// отдаёт массив целых линий. Хвост без терминатора остаётся в буфере.
// FNC1 (0x1D) — часть GS1 DataMatrix, НЕ терминатор: сохраняется внутри линии как есть.
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
