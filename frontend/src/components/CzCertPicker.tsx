import { useEffect, useState } from 'react'
import { CertInfo, listCertificates } from '../lib/cprob'

interface Props {
  value: string | null
  onChange: (thumbprint: string, subject: string) => void
  disabled?: boolean
}

export function CzCertPicker({ value, onChange, disabled }: Props) {
  const [certs, setCerts] = useState<CertInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    listCertificates()
      .then((list) => {
        if (cancelled) return
        setCerts(list)
        if (list.length === 1 && !value) {
          onChange(list[0].thumbprint, list[0].subjectName)
        }
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)))
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return <div className="alert alert--error">Не удалось получить сертификаты: {error}</div>
  }
  if (certs === null) {
    return <p className="hint">Загрузка списка сертификатов…</p>
  }
  if (certs.length === 0) {
    return (
      <div className="alert alert--error">
        Не найдено сертификатов с приватным ключом. Установите УКЭП в КриптоПро.
      </div>
    )
  }

  return (
    <select
      className="ui-select"
      style={{ minWidth: 0, width: '100%' }}
      disabled={disabled}
      value={value ?? ''}
      onChange={(e) => {
        const thumb = e.target.value
        const cert = certs.find((c) => c.thumbprint === thumb)
        if (cert) onChange(cert.thumbprint, cert.subjectName)
      }}
    >
      <option value="" disabled>— выберите сертификат —</option>
      {certs.map((c) => (
        <option key={c.thumbprint} value={c.thumbprint}>
          {prettySubject(c.subjectName)} (до {new Date(c.validTo).toLocaleDateString('ru-RU')})
        </option>
      ))}
    </select>
  )
}

function prettySubject(dn: string): string {
  const parts = dn.split(',').map((s) => s.trim())
  const cn = parts.find((p) => p.startsWith('CN='))?.slice(3)
  const o = parts.find((p) => p.startsWith('O='))?.slice(2)
  if (cn && o) return `${cn} — ${o}`
  return cn || dn
}
