import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { scansApi } from '../api/client'

// Printable ASCII stays literal. Controls and non-ASCII COM bytes get a visible hex label.
function visible(text: string) {
  return text.replace(/[^\x20-\x7e]/g, ch => `<0x${ch.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0')}>`)
}

export function SerialDiagnostics({ documentId, code }: { documentId: string; code: string }) {
  const [open, setOpen] = useState(false)
  const result = useQuery({
    queryKey: ['serial-diagnostics', documentId, code],
    queryFn: async () => (await scansApi.serialAttempts(documentId, code)).data,
    enabled: open,
  })
  return <details onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>Диагностика COM-скана</summary>
    {open && <div style={{ overflowWrap: 'anywhere' }}>
      {result.isPending && <p>Загрузка…</p>}
      {result.isError && <p role="alert">Не удалось загрузить диагностику. <button type="button" onClick={() => void result.refetch()}>Повторить</button></p>}
      {result.data?.length === 0 && <p>Для этой марки нет сохранённых COM-сканов за последние 7 дней.</p>}
      {Boolean(result.data?.length) && <p>Буквы GS занимают два байта <code>47 53</code>. Разделитель — один байт <code>1D</code>. Некоторые сканеры передают его как <code>F8</code>.</p>}
      {result.data?.map((attempt, index) => {
        const bytes = attempt.raw_hex.match(/../g) ?? []
        const raw = bytes.map(byte => String.fromCharCode(parseInt(byte, 16))).join('')
        return <div key={`${attempt.received_at}-${index}`} style={{ marginBlock: 8 }}>
          <strong>{new Date(attempt.received_at).toLocaleString('ru-RU')}</strong>
          <div>С COM-порта <code>{visible(raw)}</code></div>
          <div>Байты COM (hex) <code>{bytes.join(' ').toUpperCase()}</code></div>
          <div>Сервер получил <code>{visible(attempt.received_code)}</code></div>
        </div>
      })}
    </div>}
  </details>
}
