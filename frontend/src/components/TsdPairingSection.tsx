import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QRCodeSVG } from 'qrcode.react'
import { organizationProfilesApi, tsdAdminApi, type TsdDeviceInfo } from '../api/client'
import { TSD_APK_PATH } from '../lib/tsdLinks'

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
    onSuccess: (_, id) => {
      qc.setQueryData<TsdDeviceInfo[]>(['tsd-devices'], (items) => items?.filter((device) => device.id !== id))
      void qc.invalidateQueries({ queryKey: ['tsd-devices'] })
    },
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
        Установите приложение на Android 8.0 или новее и подключите устройство свежим QR.
        Сканер: ввод с клавиатуры и Enter. Для работы требуется интернет.
      </p>
      <div className="tsd-pairing-card">
        <div className="tsd-pairing-card__qr">
          <QRCodeSVG value={`${window.location.origin}${TSD_APK_PATH}`} size={180} level="M" />
        </div>
        <div>
          <h3>Скачать приложение для ТСД</h3>
          <p className="hint">Отсканируйте QR камерой терминала, скачайте APK и установите его. Тестовая версия 0.1.1.</p>
          <a className="button button--primary" href={TSD_APK_PATH} download>Скачать APK</a>
          <p className="hint mt-8">После установки откройте «Скандата ТСД» и отсканируйте QR подключения ниже. Привязка Chrome/PWA в APK не переносится.</p>
          <a href="/tsd" target="_blank" rel="noopener noreferrer">Открыть PWA в браузере</a>
        </div>
      </div>
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
            <p className="hint">Откройте установленное приложение «Скандата ТСД» и считайте QR аппаратным сканером в поле подключения. Для PWA можно открыть QR камерой терминала.</p>
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
                <button
                  type="button"
                  className="button button--danger button--sm"
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate(device.id)}
                >
                  Отключить
                </button>
            </div>
          ))}
        </div>
      ) : <p className="hint mt-12">Нет подключённых ТСД.</p>}
    </section>
  )
}
