import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { documentsApi, scansApi, type DocumentKind } from '../api/client'
import { useScanStore } from '../store/scanStore'

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
