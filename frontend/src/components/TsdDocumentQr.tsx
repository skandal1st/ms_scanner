import { useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'

export function TsdDocumentQr({ moyskladId, name }: { moyskladId: string; name: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="button button--sm" onClick={() => setOpen(true)}>
        QR для ТСД
      </button>
      {open ? (
        <div className="tsd-qr-overlay" role="dialog" aria-modal="true" aria-label="QR отгрузки">
          <div className="tsd-qr-dialog">
            <h2>Открыть на ТСД</h2>
            <p>{name}</p>
            <QRCodeSVG value={`SKANDATA:DOCUMENT:${moyskladId}`} size={260} level="M" />
            <p className="hint">QR выбирает документ, но не содержит токен авторизации.</p>
            <button type="button" className="button button--primary" onClick={() => setOpen(false)}>
              Закрыть
            </button>
          </div>
        </div>
      ) : null}
    </>
  )
}
