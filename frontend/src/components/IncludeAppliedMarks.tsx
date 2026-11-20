import { useState } from 'react'
import { scansApi } from '../api/client'
import { useScanStore } from '../store/scanStore'
import { useModal } from './ModalProvider'

export function IncludeAppliedMarks({ documentId, scanIds, single = false, disabled = false }: {
  documentId: string
  scanIds: string[]
  single?: boolean
  disabled?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const modal = useModal()
  async function include() {
    setBusy(true)
    try {
      const ok = await modal.confirm(
        `Честный Знак подтвердил нанесение. Ввод в оборот ещё ожидается. Добавить в отгрузку МойСклад выбранные марки (${scanIds.length})?`,
        { title: 'Включить марки до ввода в оборот?', variant: 'warn', okText: 'Включить в отгрузку', focusCancel: true },
      )
      if (!ok || useScanStore.getState().document?.id !== documentId) return
      const { data } = await scansApi.includeApplied(documentId, scanIds)
      const store = useScanStore.getState()
      if (store.document?.id !== documentId) return
      const updated = new Map(data.map(scan => [scan.id, scan]))
      store.setScans(store.scans.map(scan => updated.get(scan.id) ?? scan))
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
      void modal.alert(detail || 'Не удалось включить марки в отгрузку. Обновите сборку.', { variant: 'error' })
    } finally {
      setBusy(false)
    }
  }
  return <button type="button" className="button" disabled={busy || disabled} onClick={() => void include()}>
    {single ? 'Включить эту марку в отгрузку' : `Включить марки до ввода в оборот (${scanIds.length})`}
  </button>
}
