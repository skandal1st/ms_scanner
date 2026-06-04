// Минимальный shim для Web Serial API (https://wicg.github.io/serial/).
// На момент TS 5.6 типы ещё не входят в стандартный lib.dom.d.ts.

interface SerialPortInfo {
  usbVendorId?: number
  usbProductId?: number
}

interface SerialOptions {
  baudRate: number
  dataBits?: 7 | 8
  stopBits?: 1 | 2
  parity?: 'none' | 'even' | 'odd'
  bufferSize?: number
  flowControl?: 'none' | 'hardware'
}

interface SerialPort extends EventTarget {
  readonly readable: ReadableStream<Uint8Array> | null
  readonly writable: WritableStream<Uint8Array> | null
  getInfo(): SerialPortInfo
  open(options: SerialOptions): Promise<void>
  close(): Promise<void>
  forget?(): Promise<void>
  addEventListener(type: 'connect' | 'disconnect', listener: (this: SerialPort, ev: Event) => void): void
  removeEventListener(type: 'connect' | 'disconnect', listener: (this: SerialPort, ev: Event) => void): void
}

interface SerialPortRequestOptions {
  filters?: Array<{ usbVendorId?: number; usbProductId?: number }>
}

interface Serial extends EventTarget {
  getPorts(): Promise<SerialPort[]>
  requestPort(options?: SerialPortRequestOptions): Promise<SerialPort>
  addEventListener(type: 'connect' | 'disconnect', listener: (this: Serial, ev: Event & { target: SerialPort }) => void): void
  removeEventListener(type: 'connect' | 'disconnect', listener: (this: Serial, ev: Event & { target: SerialPort }) => void): void
}

interface Navigator {
  readonly serial: Serial
}
