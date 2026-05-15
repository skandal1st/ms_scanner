import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { integrationsApi } from '../api/client'

interface SettingsPageProps {
  embedded?: boolean
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
      </div>
    </div>
  )
}
