import type { UseSerialScannerResult } from '../hooks/useSerialScanner'

export function SerialScannerStatus({ serial }: { serial: UseSerialScannerResult }) {
  return <div className="serial-scanner-status">
    <div className="flex-row gap-8" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
      <span className={`badge ${serial.connected ? 'badge--ok' : 'badge--warn'}`}><span className="badge__dot" />COM-порт {serial.connected ? 'подключён' : 'не подключён'}</span>
      <button type="button" className="button button--sm" disabled={!serial.supported}
        onClick={() => void (serial.connected ? serial.disconnect() : serial.requestConnect())}>
        {serial.connected ? 'Отключить сканер' : 'Подключить COM-порт'}
      </button>
    </div>
    {!serial.supported && <p className="hint">COM-сканирование доступно в Chrome и Edge по HTTPS. Можно ввести код вручную.</p>}
    {serial.supported && serial.error && <p role="status" className="hint">{serial.error}</p>}
  </div>
}
