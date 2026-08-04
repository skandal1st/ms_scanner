import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  documentsApi,
  scansApi,
  integrationsApi,
  productsApi,
  type DocumentKind,
} from '../api/client'
import { useScanStore } from '../store/scanStore'

/** Интеграция текущего пользователя (в т.ч. cz_inn — владелец подписи для сверки марок). */
export function useIntegration() {
  return useQuery({
    queryKey: ['integration'],
    queryFn: () => integrationsApi.get().then((r) => r.data),
    staleTime: 60_000,
  })
}

/** Авто-подсказки сопоставления GTIN↔товар для документа (МС-поиск по имени из ЧЗ/УПД).
 * enabled — включать только когда есть что сопоставлять (запросы к МС дорогие). */
export function useMatchSuggestions(documentId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['match-suggestions', documentId],
    queryFn: () => productsApi.matchSuggestions(documentId as string).then((r) => r.data),
    enabled: Boolean(documentId) && enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  })
}

export function useMsDocuments(kind: DocumentKind, search?: string) {
  return useQuery({
    queryKey: ['ms-docs', kind, search ?? ''],
    queryFn: () => documentsApi.listMs(kind, search).then((r) => r.data),
    staleTime: 60_000,
  })
}

export function useDocuments(kind?: DocumentKind) {
  return useQuery({
    queryKey: ['documents', kind ?? 'all'],
    queryFn: () => documentsApi.list(kind).then((r) => r.data),
  })
}

export function useCreateDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      name,
      kind,
      moysklad_id,
    }: {
      name: string
      kind: DocumentKind
      moysklad_id?: string
    }) => documentsApi.create(name, kind, moysklad_id).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['documents'] }),
  })
}

export function useLoadDocument(documentId: string | null) {
  const { setDocument, setScans } = useScanStore()

  return useQuery({
    queryKey: ['document', documentId],
    queryFn: async () => {
      if (!documentId) return null
      const [{ data: doc }, { data: scans }] = await Promise.all([
        documentsApi.get(documentId),
        scansApi.list(documentId),
      ])
      setDocument(doc)
      setScans(scans)
      return doc
    },
    enabled: !!documentId,
  })
}

export function useProcessDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => documentsApi.process(id).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['documents'] }),
  })
}

export function useClearDocumentScans() {
  const qc = useQueryClient()
  const setScans = useScanStore((s) => s.setScans)
  return useMutation({
    mutationFn: (id: string) => scansApi.clearDocument(id),
    onSuccess: (_data, id) => {
      setScans([]) // очищаем локально, документ остаётся выбранным
      qc.invalidateQueries({ queryKey: ['document', id] })
    },
  })
}
