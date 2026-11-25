import { useState } from 'react'
import { scansApi, type ShipmentInclusionReason } from '../api/client'
import { useScanStore } from '../store/scanStore'
import { useModal } from './ModalProvider'

export function IncludeShipmentMarks({ documentId, scanIds, reason, single = false, disabled = false }: {
  documentId: string
  scanIds: string[]
  reason: ShipmentInclusionReason
  single?: boolean
  disabled?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const modal = useModal()
  const withdrawn = reason === 'withdrawn'
  const action = withdrawn ? 'Включить выбывшие марки' : 'Включить марки до ввода в оборот'
  async function include() {
    setBusy(true)
    try {
      const ok = await modal.confirm(
        withdrawn
          ? `Честный Знак подтвердил выбытие выбранных марок (${scanIds.length}). При отправке их коды будут записаны в отгрузку МойСклад. В Честном Знаке марки останутся выведенными из оборота.`
          : `Честный Знак подтвердил нанесение. Ввод в оборот ещё ожидается. Добавить в отгрузку МойСклад выбранные марки (${scanIds.length})?`,
        { title: action + '?', variant: 'warn', okText: withdrawn ? action : 'Включить в отгрузку', danger: withdrawn, focusCancel: true },
      )
      if (!ok || useScanStore.getState().document?.id !== documentId) return
      const { data } = await scansApi.includeShipmentMarks(documentId, scanIds, reason)
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
    {single ? (withdrawn ? 'Включить выбывшую марку в отгрузку' : 'Включить эту марку в отгрузку') : `${action} (${scanIds.length})`}
  </button>
}
