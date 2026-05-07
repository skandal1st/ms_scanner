import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { documentsApi, scansApi } from '../api/client'
import { useScanStore } from '../store/scanStore'

export function useMsSupplies() {
  return useQuery({
    queryKey: ['ms-supplies'],
    queryFn: () => documentsApi.listMsSupplies().then((r) => r.data),
    staleTime: 60_000,
  })
}

export function useDocuments() {
  return useQuery({
    queryKey: ['documents'],
    queryFn: () => documentsApi.list().then((r) => r.data),
  })
}

export function useCreateDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, moysklad_id }: { name: string; moysklad_id?: string }) =>
      documentsApi.create(name, moysklad_id).then((r) => r.data),
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

export function useAcceptDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => documentsApi.accept(id).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['documents'] }),
  })
}
