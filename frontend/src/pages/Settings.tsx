import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { integrationsApi, czAuthApi, Integration } from '../api/client'
import { detectPlugin, signCadesBes, PluginStatus } from '../lib/cprob'
import { CzCertPicker } from '../components/CzCertPicker'

interface SettingsPageProps {
  /** В iframe МС ручная привязка токена не нужна — токен уже выдан Vendor API. */
  embedded?: boolean
}

export function SettingsPage({ embedded = false }: SettingsPageProps = {}) {
  const [msToken, setMsToken] = useState('')
  const [pluginStatus, setPluginStatus] = useState<PluginStatus | 'checking'>('checking')
  const [certThumb, setCertThumb] = useState<string | null>(null)
  const [certSubject, setCertSubject] = useState<string | null>(null)
  const [czError, setCzError] = useState<string | null>(null)
  const qc = useQueryClient()

  const { data: integration } = useQuery({
    queryKey: ['integration'],
    queryFn: () => integrationsApi.get().then((r) => r.data),
  })

  const updateMutation = useMutation({
    mutationFn: (data: { moysklad_token?: string }) =>
      integrationsApi.update(data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['integration'] }),
  })

  useEffect(() => {
    detectPlugin().then(setPluginStatus)
  }, [])

  const czLoginMutation = useMutation({
    mutationFn: async () => {
      setCzError(null)
      if (!certThumb || !certSubject) throw new Error('Выберите сертификат')
      const { data: ch } = await czAuthApi.challenge()
      const signed = await signCadesBes(certThumb, ch.data)
      const { data } = await czAuthApi.login({
        uuid: ch.uuid,
        signed_data: signed,
        cert_thumbprint: certThumb,
        cert_subject: certSubject,
      })
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['integration'] }),
    onError: (e: any) => setCzError(e?.response?.data?.detail || e?.message || 'Ошибка входа'),
  })

  const czLogoutMutation = useMutation({
    mutationFn: () => czAuthApi.logout().then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['integration'] }),
  })

  return (
    <div className="settings-page">
      <div className="settings-card">
        <h1>Настройки интеграций</h1>

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
          {!embedded && (
            <>
              <p className="hint">
                Вставьте API-токен МойСклад (Настройки → Доступ к API).
                Или используйте <a href="/api/auth/moysklad/login">OAuth авторизацию</a>.
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
                  disabled={!msToken.trim() || updateMutation.isPending}
                  onClick={() => updateMutation.mutate({ moysklad_token: msToken })}
                >
                  Сохранить
                </button>
              </div>
            </>
          )}
        </section>

        <CzSection
          integration={integration}
          pluginStatus={pluginStatus}
          certThumb={certThumb}
          onCertChange={(t, s) => { setCertThumb(t); setCertSubject(s) }}
          onLogin={() => czLoginMutation.mutate()}
          onLogout={() => czLogoutMutation.mutate()}
          loginPending={czLoginMutation.isPending}
          logoutPending={czLogoutMutation.isPending}
          error={czError}
        />

        {updateMutation.isSuccess && (
          <div className="alert alert--ok mt-12">Настройки сохранены</div>
        )}
      </div>
    </div>
  )
}

interface CzSectionProps {
  integration: Integration | undefined
  pluginStatus: PluginStatus | 'checking'
  certThumb: string | null
  onCertChange: (thumb: string, subject: string) => void
  onLogin: () => void
  onLogout: () => void
  loginPending: boolean
  logoutPending: boolean
  error: string | null
}

function CzSection(p: CzSectionProps) {
  const isMock = p.integration?.cz_auth_method === 'mock'
  const validUntil = p.integration?.cz_token_valid_until
    ? new Date(p.integration.cz_token_valid_until)
    : null
  const isLoggedIn = !!p.integration?.has_cz

  const badge = isLoggedIn
    ? { cls: 'badge--ok',    text: 'Авторизован' }
    : isMock
      ? { cls: 'badge--warn',  text: 'Mock режим' }
      : { cls: 'badge--error', text: 'Требуется вход через УКЭП' }

  return (
    <section className="section">
      <div className="section__head">
        <h2 style={{ margin: 0 }}>Честный Знак</h2>
        <span className={`badge ${badge.cls}`}>{badge.text}</span>
      </div>

      {isLoggedIn && (
        <div className="settings-status">
          {p.integration?.cz_cert_subject && (
            <div className="settings-status__row">
              <span className="settings-status__label">Сертификат:</span>
              <span className="settings-status__value">{p.integration.cz_cert_subject}</span>
            </div>
          )}
          {validUntil && (
            <div className="settings-status__row">
              <span className="settings-status__label">Действует до:</span>
              <span className="settings-status__value">{validUntil.toLocaleString('ru-RU')}</span>
            </div>
          )}
        </div>
      )}

      {isMock ? (
        <>
          <p className="hint">
            Сервер запущен в mock-режиме (<code>CZ_AUTH_METHOD=mock</code>) — реальный
            ЧЗ не вызывается. Кнопка ниже выдаст фейковый токен на 1 час, чтобы можно
            было протестировать UI.
          </p>
          <div className="buttons mt-8">
            <button
              type="button"
              className="button button--success"
              disabled={p.loginPending}
              onClick={p.onLogin}
            >
              {p.loginPending ? 'Создаём mock-токен…' : 'Mock-вход (dev only)'}
            </button>
            {isLoggedIn && (
              <button
                type="button"
                className="button"
                disabled={p.logoutPending}
                onClick={p.onLogout}
              >
                {p.logoutPending ? '…' : 'Выйти'}
              </button>
            )}
          </div>
        </>
      ) : p.pluginStatus === 'checking' ? (
        <p className="hint">Проверка КриптоПро Browser Plugin…</p>
      ) : p.pluginStatus === 'absent' ? (
        <div className="alert alert--error">
          КриптоПро Browser Plugin не обнаружен.{' '}
          <a href="https://www.cryptopro.ru/products/cades/plugin" target="_blank" rel="noreferrer">
            Установить
          </a>
          {' '}и перезагрузить страницу.
        </div>
      ) : (
        <>
          <p className="hint">
            Выберите сертификат УКЭП, которым вы зарегистрированы в Честном Знаке.
            Подпись не покидает ваш компьютер — мы получаем только готовый CAdES-BES blob.
          </p>
          <div className="mb-12">
            <CzCertPicker
              value={p.certThumb}
              onChange={p.onCertChange}
              disabled={p.loginPending}
            />
          </div>
          <div className="buttons">
            <button
              type="button"
              className="button button--success"
              disabled={!p.certThumb || p.loginPending}
              onClick={p.onLogin}
            >
              {p.loginPending
                ? 'Подписываем challenge…'
                : isLoggedIn
                  ? 'Обновить токен'
                  : 'Войти через УКЭП'}
            </button>
            {isLoggedIn && (
              <button
                type="button"
                className="button"
                disabled={p.logoutPending}
                onClick={p.onLogout}
              >
                {p.logoutPending ? '…' : 'Выйти'}
              </button>
            )}
          </div>
        </>
      )}

      {p.error && <div className="alert alert--error mt-8">{p.error}</div>}
    </section>
  )
}
