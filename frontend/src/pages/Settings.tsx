import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { czApi, integrationsApi } from '../api/client'
import type { Integration } from '../api/client'
import {
  isWebSerialSupported,
  setScannerMode,
  useScannerMode,
} from '../lib/scannerMode'
import {
  isPluginAvailable,
  listCertificates,
  signDataCadesBes,
  type CzCertificate,
} from '../lib/cprob'

interface SettingsPageProps {
  embedded?: boolean
}

function describePort(port: SerialPort): string {
  const info = port.getInfo()
  if (info.usbVendorId != null && info.usbProductId != null) {
    const v = info.usbVendorId.toString(16).padStart(4, '0').toUpperCase()
    const p = info.usbProductId.toString(16).padStart(4, '0').toUpperCase()
    return `USB ${v}:${p}`
  }
  return 'Системный COM-порт'
}

function ScannerSection() {
  const mode = useScannerMode()
  const supported = isWebSerialSupported()
  const [ports, setPorts] = useState<SerialPort[]>([])
  const [error, setError] = useState<string | null>(null)

  const refreshPorts = async () => {
    if (!supported) return
    try {
      const list = await navigator.serial.getPorts()
      setPorts(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    void refreshPorts()
    if (!supported) return
    const onChange = () => void refreshPorts()
    navigator.serial.addEventListener('connect', onChange)
    navigator.serial.addEventListener('disconnect', onChange)
    return () => {
      navigator.serial.removeEventListener('connect', onChange)
      navigator.serial.removeEventListener('disconnect', onChange)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supported])

  const handleRequest = async () => {
    setError(null)
    try {
      await navigator.serial.requestPort()
      await refreshPorts()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (!msg.toLowerCase().includes('no port selected') && !msg.includes('NotFoundError')) {
        setError(`Не удалось подключить порт: ${msg}`)
      }
    }
  }

  const handleForget = async (port: SerialPort) => {
    setError(null)
    try {
      if (typeof port.forget === 'function') {
        await port.forget()
      } else {
        await port.close()
      }
      await refreshPorts()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="section">
      <div className="section__head">
        <h2 style={{ margin: 0 }}>Сканер</h2>
        <span className={`badge ${mode === 'com' ? 'badge--info' : 'badge--pending'}`}>
          {mode === 'com' ? 'COM-порт' : 'USB (клавиатура)'}
        </span>
      </div>

      <p className="hint">
        Выберите способ ввода кодов маркировки. По умолчанию используется USB-сканер
        в режиме эмуляции клавиатуры. Если сканер настроен на virtual COM port
        (USB-CDC) — выберите COM-режим.
      </p>

      <div className="field-row mt-8" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 8 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
          <input
            type="radio"
            name="scanner-mode"
            value="keyboard"
            checked={mode === 'keyboard'}
            onChange={() => setScannerMode('keyboard')}
          />
          USB-сканер (клавиатура)
        </label>
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            cursor: supported ? 'pointer' : 'not-allowed',
            opacity: supported ? 1 : 0.55,
          }}
        >
          <input
            type="radio"
            name="scanner-mode"
            value="com"
            checked={mode === 'com'}
            onChange={() => setScannerMode('com')}
            disabled={!supported}
          />
          COM-порт (Web Serial API)
        </label>
      </div>

      {!supported && (
        <div className="alert alert--warn mt-12">
          Ваш браузер не поддерживает Web Serial API. Используйте Chrome или Edge
          по защищённому соединению (HTTPS).
        </div>
      )}

      {supported && mode === 'com' && (
        <>
          <div className="mt-12">
            {ports.length > 0 ? (
              <>
                <p className="hint" style={{ marginBottom: 4 }}>Авторизованные порты:</p>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {ports.map((port, i) => (
                    <li key={i} style={{ marginBottom: 4 }}>
                      <code>{describePort(port)}</code>{' '}
                      <button
                        type="button"
                        className="button button--link"
                        onClick={() => void handleForget(port)}
                      >
                        отвязать
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <span className="badge badge--warn">Нет авторизованных портов</span>
            )}
          </div>

          <div className="field-row mt-12">
            <button
              type="button"
              className="button button--success"
              onClick={() => void handleRequest()}
            >
              Подключить COM-порт
            </button>
          </div>

          <p className="hint mt-8">
            Настройте сканер на режим Virtual COM Port (USB-CDC): 9600 бод, 8 бит данных,
            без бита чётности, 1 стоп-бит, окончание строки CR/LF. Параметры обычно
            переключаются сервисными штрих-кодами из мануала сканера.
          </p>

          {error && (
            <div className="alert alert--error mt-12">{error}</div>
          )}
        </>
      )}
    </section>
  )
}

export function SettingsPage({ embedded = false }: SettingsPageProps) {
  const [msToken, setMsToken] = useState('')
  const qc = useQueryClient()

  const { data: integration } = useQuery({
    queryKey: ['integration'],
    queryFn: () => integrationsApi.get().then((r) => r.data),
  })

  const patchIntegration = useMutation({
    mutationFn: (data: { moysklad_token?: string }) =>
      integrationsApi.update(data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['integration'] })
      window.setTimeout(() => patchIntegration.reset(), 3500)
      setMsToken('')
    },
  })

  return (
    <div className="settings-page">
      <div className="settings-card">
        <h1>Настройки интеграции</h1>

        <section className="section">
          <div className="section__head">
            <h2 style={{ margin: 0 }}>МойСклад</h2>
            <span className={`badge ${integration?.has_moysklad ? 'badge--ok' : 'badge--error'}`}>
              {integration?.has_moysklad ? 'Подключён' : 'Не подключён'}
            </span>
          </div>

          {integration?.moysklad_account_name && (
            <p className="hint">Аккаунт: <b>{integration.moysklad_account_name}</b></p>
          )}

          <p className="hint">
            Приложение использует доступ к МойСклад, чтобы загрузить отгрузочные документы,
            показать план сборки и записать коды маркировки в позиции документа.
          </p>

          {!embedded && (
            <>
              <p className="hint">
                Вставьте API-токен МойСклад или используйте{' '}
                <a href="/api/auth/moysklad/login">OAuth-авторизацию</a>.
              </p>
              <div className="field-row mt-8">
                <input
                  type="password"
                  value={msToken}
                  onChange={(e) => setMsToken(e.target.value)}
                  placeholder="Bearer токен МойСклад"
                  className="ui-input ui-input--block"
                  style={{ fontFamily: 'monospace' }}
                />
                <button
                  type="button"
                  className="button button--success"
                  disabled={!msToken.trim() || patchIntegration.isPending}
                  onClick={() => patchIntegration.mutate({ moysklad_token: msToken })}
                >
                  Сохранить
                </button>
              </div>
            </>
          )}

          {patchIntegration.isSuccess && (
            <div className="alert alert--ok mt-12">Настройки сохранены</div>
          )}
          {patchIntegration.isError && (
            <div className="alert alert--error mt-12">Не удалось сохранить настройки</div>
          )}
        </section>

        {!embedded && <ChestnyZnakSection integration={integration} />}
        {!embedded && <ScannerSection />}
      </div>
    </div>
  )
}

function ChestnyZnakSection({
  integration,
}: {
  integration?: Integration | undefined
}) {
  const qc = useQueryClient()
  const [pluginAvailable, setPluginAvailable] = useState<boolean | null>(null)
  const [certs, setCerts] = useState<CzCertificate[]>([])
  const [selectedThumbprint, setSelectedThumbprint] = useState('')
  const [loadingCerts, setLoadingCerts] = useState(false)
  const [signing, setSigning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  // Расширение CryptoPro инжектирует API асинхронно — ждём (до 5 сек).
  const recheckPlugin = async () => {
    setPluginAvailable(null)
    const ok = await isPluginAvailable()
    setPluginAvailable(ok)
  }

  useEffect(() => {
    let cancelled = false
    void isPluginAvailable().then((ok) => {
      if (!cancelled) setPluginAvailable(ok)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const refreshCerts = async () => {
    setError(null)
    setLoadingCerts(true)
    try {
      const list = await listCertificates()
      setCerts(list)
      if (list.length && !selectedThumbprint) {
        setSelectedThumbprint(list[0].thumbprint)
      }
      if (!list.length) {
        setError(
          'В хранилище личных сертификатов не найдено действующих сертификатов. Подключите УКЭП-токен.',
        )
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingCerts(false)
    }
  }

  const handleLogin = async () => {
    if (!selectedThumbprint) {
      setError('Выберите сертификат для входа')
      return
    }
    setError(null)
    setSuccess(false)
    setSigning(true)
    try {
      const { data: challenge } = await czApi.challenge()
      const signature = await signDataCadesBes(selectedThumbprint, challenge.data)
      const cert = certs.find((c) => c.thumbprint === selectedThumbprint)
      await czApi.login({
        uuid: challenge.uuid,
        signed_data: signature,
        cert_thumbprint: selectedThumbprint,
        cert_subject: cert?.subject,
      })
      qc.invalidateQueries({ queryKey: ['integration'] })
      setSuccess(true)
      window.setTimeout(() => setSuccess(false), 4000)
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      const msg = ax?.response?.data?.detail
      setError(
        msg
          ? `Не удалось войти в ЧЗ: ${msg}`
          : e instanceof Error
            ? e.message
            : String(e),
      )
    } finally {
      setSigning(false)
    }
  }

  const handleLogout = async () => {
    setError(null)
    try {
      await czApi.logout()
      qc.invalidateQueries({ queryKey: ['integration'] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const validUntil = integration?.cz_token_valid_until
    ? new Date(integration.cz_token_valid_until)
    : null
  const isExpired =
    validUntil != null && validUntil.getTime() <= Date.now()
  const isConnected = !!integration?.has_cz && !isExpired

  return (
    <section className="section">
      <div className="section__head">
        <h2 style={{ margin: 0 }}>Честный Знак</h2>
        <span className={`badge ${isConnected ? 'badge--ok' : 'badge--error'}`}>
          {isConnected ? 'Подключён' : 'Не подключён'}
        </span>
      </div>

      <p className="hint">
        Авторизация через УКЭП — позволяет проверять статус кодов маркировки
        напрямую в ЧЗ во время сканирования и распаковывать SSCC-коробки.
      </p>

      {isConnected && (
        <div className="hint mt-8">
          {integration?.cz_cert_subject && (
            <div>Сертификат: <b>{integration.cz_cert_subject}</b></div>
          )}
          {validUntil && (
            <div>Действует до: {validUntil.toLocaleString('ru')}</div>
          )}
          <div className="field-row mt-8">
            <button type="button" className="button" onClick={() => void handleLogout()}>
              Выйти
            </button>
          </div>
        </div>
      )}

      {!isConnected && (
        <>
          {pluginAvailable === null && (
            <div className="hint mt-12">Проверяю наличие плагина КриптоПро…</div>
          )}
          {pluginAvailable === false && (
            <div className="alert alert--warn mt-12">
              <div>КриптоПро Browser Plugin не обнаружен в браузере.</div>
              <ol style={{ margin: '8px 0 0 18px', padding: 0, fontSize: 12 }}>
                <li>
                  Установить{' '}
                  <a
                    href="https://www.cryptopro.ru/products/cades/plugin"
                    target="_blank"
                    rel="noreferrer"
                  >
                    плагин КриптоПро
                  </a>{' '}
                  (CSP + Browser Plug-in).
                </li>
                <li>
                  Поставить{' '}
                  <a
                    href="https://chromewebstore.google.com/detail/cryptopro-extension-for-c/iifchhfnnmpdbibifmljnfjhpififfog"
                    target="_blank"
                    rel="noreferrer"
                  >
                    расширение для Chrome
                  </a>{' '}
                  (или Edge).
                </li>
                <li>В иконке расширения убедиться, что для skandata.ru разрешён доступ.</li>
                <li>Полностью перезагрузить страницу (Ctrl+Shift+R).</li>
              </ol>
              <div className="field-row mt-8">
                <button
                  type="button"
                  className="button"
                  onClick={() => void recheckPlugin()}
                >
                  Проверить ещё раз
                </button>
              </div>
            </div>
          )}
          {pluginAvailable === true && (
            <>
              <div className="field-row mt-8">
                <button
                  type="button"
                  className="button"
                  onClick={() => void refreshCerts()}
                  disabled={loadingCerts}
                >
                  {loadingCerts ? 'Загружаю…' : 'Найти сертификаты'}
                </button>
              </div>
              {certs.length > 0 && (
                <div className="field-row mt-8">
                  <select
                    className="ui-select"
                    style={{ minWidth: 0, width: '100%' }}
                    value={selectedThumbprint}
                    onChange={(e) => setSelectedThumbprint(e.target.value)}
                  >
                    {certs.map((c) => (
                      <option key={c.thumbprint} value={c.thumbprint}>
                        {c.subject.length > 80
                          ? `${c.subject.slice(0, 80)}…`
                          : c.subject}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="field-row mt-12">
                <button
                  type="button"
                  className="button button--success"
                  onClick={() => void handleLogin()}
                  disabled={signing || !selectedThumbprint}
                >
                  {signing ? 'Подписываю…' : 'Войти через УКЭП'}
                </button>
              </div>
            </>
          )}
        </>
      )}

      {success && (
        <div className="alert alert--ok mt-12">Вход в Честный Знак выполнен.</div>
      )}
      {error && <div className="alert alert--error mt-12">{error}</div>}
    </section>
  )
}
