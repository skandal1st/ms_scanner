import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { organizationProfilesApi } from '../api/client'
import { getOrganizationProfileId, setOrganizationProfileId } from '../lib/organizationProfile'
import { useScanStore } from '../store/scanStore'

export function OrganizationProfileSwitcher({ compact = false }: { compact?: boolean }) {
  const queryClient = useQueryClient()
  const profilesQuery = useQuery({
    queryKey: ['organization-profiles'],
    queryFn: () => organizationProfilesApi.list().then((r) => r.data),
  })
  const profiles = profilesQuery.data ?? []
  const selectId = compact ? 'organization-profile-nav' : 'organization-profile-settings'
  const storedId = getOrganizationProfileId()
  const active = profiles.find((p) => p.id === storedId)
    ?? profiles.find((p) => p.is_default)
    ?? profiles[0]

  useEffect(() => {
    if (active && active.id !== storedId) setOrganizationProfileId(active.id)
  }, [active, storedId])

  const sync = useMutation({
    mutationFn: () => organizationProfilesApi.sync().then((r) => r.data),
    onSuccess: async (items) => {
      const next = items.find((p) => p.id === getOrganizationProfileId())
        ?? items.find((p) => p.is_default)
        ?? items[0]
      setOrganizationProfileId(next?.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['organization-profiles'] }),
        queryClient.invalidateQueries({ queryKey: ['integration'] }),
      ])
    },
  })

  const switchProfile = (id: string) => {
    if (!id || id === active?.id) return
    setOrganizationProfileId(id)
    useScanStore.getState().reset()
    // Профиль влияет на все документы, ЧЗ и кеши React Query. Полная перезагрузка
    // гарантирует, что ни один запрос старого юрлица не дорисует устаревшие данные.
    window.location.reload()
  }

  if (profilesQuery.isLoading) return compact ? null : <p className="hint">Загружаем юрлица…</p>

  return (
    <div
      className="field-row"
      style={{ alignItems: 'center', gap: 8, flexWrap: compact ? 'nowrap' : 'wrap' }}
    >
      {!compact && <label htmlFor={selectId}><b>Активное юрлицо</b></label>}
      <select
        id={selectId}
        className="ui-input"
        value={active?.id ?? ''}
        onChange={(e) => switchProfile(e.target.value)}
        aria-label="Активное юрлицо"
        style={{ minWidth: compact ? 180 : 260, maxWidth: compact ? 280 : 420 }}
      >
        {profiles.length === 0 && <option value="">Юрлица не загружены</option>}
        {profiles.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.name}{profile.has_cz ? '' : ' · ЧЗ не подключён'}
          </option>
        ))}
      </select>
      {!compact && (
        <button
          type="button"
          className="button"
          disabled={sync.isPending}
          onClick={() => sync.mutate()}
        >
          {sync.isPending ? 'Обновляем…' : 'Обновить из МойСклад'}
        </button>
      )}
      {sync.isError && !compact && (
        <span className="hint" style={{ color: 'var(--st-err-fg)' }}>
          Не удалось получить юрлица. Проверьте права решения в МойСклад.
        </span>
      )}
    </div>
  )
}
