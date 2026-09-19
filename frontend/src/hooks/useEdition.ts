import { useQuery } from '@tanstack/react-query'
import { integrationsApi, type Integration } from '../api/client'

export type Edition = 'ms_lite' | 'full'

/**
 * Издание аккаунта из /integrations/. Пока не загрузилось — считаем full, чтобы не
 * мигать скрытием разделов у полноценных пользователей. Гейт на сервере
 * (require_full_edition) всё равно закрывает full-only API для ms_lite.
 */
export function useEdition(): { edition: Edition; isFull: boolean; isLoading: boolean } {
  const { data, isLoading } = useQuery({
    queryKey: ['integration'],
    queryFn: () => integrationsApi.get().then((r) => r.data as Integration),
    staleTime: 60_000,
  })
  const edition: Edition = data?.edition === 'ms_lite' ? 'ms_lite' : 'full'
  return { edition, isFull: edition === 'full', isLoading }
}
