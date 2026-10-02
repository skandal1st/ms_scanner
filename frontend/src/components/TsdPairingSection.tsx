import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QRCodeSVG } from 'qrcode.react'
import { organizationProfilesApi, tsdAdminApi } from '../api/client'

export function TsdPairingSection() {
  const qc = useQueryClient()
  const [pairingOpen, setPairingOpen] = useState(false)
  const [workplaceId, setWorkplaceId] = useState('')
  const { data: profiles = [] } = useQuery({
    queryKey: ['organization-profiles'],
    queryFn: () => organizationProfilesApi.list().then((r) => r.data),
  })
  const activeProfileId = localStorage.getItem('organization_profile_id')
  const activeProfile = profiles.find((profile) => profile.id === activeProfileId)
    || profiles.find((profile) => profile.is_default)
    || profiles[0]
  const workplaces = activeProfile?.workplaces.filter((item) => item.is_active) ?? []
  useEffect(() => {
    if (workplaceId && workplaces.some((item) => item.id === workplaceId)) return
    const preferred = workplaces.find((item) => item.is_default) || workplaces[0]
    setWorkplaceId(preferred?.id || '')
  }, [workplaceId, workplaces])
  const pairing = useMutation({
    mutationFn: () => tsdAdminApi.createPairing(workplaceId || undefined).then((r) => r.data),
    onSuccess: () => setPairingOpen(true),
  })
  const { data: devices = [] } = useQuery({
    queryKey: ['tsd-devices'],
    queryFn: () => tsdAdminApi.devices().then((r) => r.data),
  })
  const revoke = useMutation({
    mutationFn: (id: string) => tsdAdminApi.revoke(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tsd-devices'] }),
  })

  return (
    <section className="section">
      <div className="section__head">
        <h2 style={{ margin: 0 }}>Терминалы сбора данных</h2>
        <span className="badge badge--info">АТОЛ · Android</span>
      </div>
      <p className="hint">
        Подключите ТСД к текущему юрлицу и рабочему месту. QR действует 5 минут и
        используется только один раз.
      </p>
      <p className="hint">
        Для тестов на Android откройте <a href="/tsd" target="_blank" rel="noopener noreferrer">мобильную версию ТСД</a>
        {' '}в Chrome и установите её на главный экран. Затем откройте приложение
        и подключите устройство свежим QR. Сканер: ввод с клавиатуры и Enter.
      </p>
      {workplaces.length > 0 ? (
        <label className="field mt-8" style={{ maxWidth: 420 }}>
          <span>Рабочее место ТСД</span>
          <select value={workplaceId} onChange={(event) => setWorkplaceId(event.target.value)}>
            {workplaces.map((workplace) => (
              <option key={workplace.id} value={workplace.id}>{workplace.name}</option>
            ))}
          </select>
        </label>
      ) : (
        <p className="hint mt-8">
          Рабочее место будет создано автоматически. После настройки складов можно
          будет подключить отдельные ТСД для каждого из них.
        </p>
      )}
      <button
        type="button"
        className="button button--primary"
        disabled={pairing.isPending}
        onClick={() => pairing.mutate()}
      >
        {pairing.isPending ? 'Создаём QR…' : 'Подключить ТСД'}
      </button>
      {pairing.error ? (
        <div className="alert alert--error mt-12">Не удалось создать QR подключения.</div>
      ) : null}

      {pairingOpen && pairing.data ? (
        <div className="tsd-pairing-card" role="dialog" aria-label="QR подключения ТСД">
          <div className="tsd-pairing-card__qr">
            <QRCodeSVG value={`${window.location.origin}/tsd?pair=${encodeURIComponent(pairing.data.code)}`} size={220} level="M" />
          </div>
          <div>
            <h3>Отсканируйте QR на ТСД</h3>
            <p><b>{pairing.data.workplace_name}</b></p>
            <p className="hint">Отсканируйте QR камерой терминала — откроется PWA и устройство подключится автоматически. Можно также считать QR в поле подключения установленной PWA.</p>
            <button type="button" className="button" onClick={() => setPairingOpen(false)}>
              Закрыть
            </button>
          </div>
        </div>
      ) : null}

      {devices.length > 0 ? (
        <div className="tsd-device-list mt-12">
          {devices.map((device) => (
            <div className="tsd-device-row" key={device.id}>
              <div>
                <b>{device.name}</b>
                <div className="hint">
                  {device.workplace_name} · {device.last_seen_at
                    ? `был в сети ${new Date(device.last_seen_at).toLocaleString('ru-RU')}`
                    : 'ещё не выходил в сеть'}
                </div>
              </div>
              {device.is_active ? (
                <button
                  type="button"
                  className="button button--danger button--sm"
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate(device.id)}
                >
                  Отключить
                </button>
              ) : <span className="badge badge--pending">Отключён</span>}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
}
