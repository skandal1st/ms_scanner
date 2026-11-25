import { useState } from 'react'
import { scansApi, type ShipmentInclusionReason } from '../api/client'
import { useScanStore } from '../store/scanStore'
import { useModal } from './ModalProvider'

const warnings = {
  applied: {
    action: 'Включить марки до ввода в оборот',
    single: 'Включить эту марку в отгрузку',
    confirm: 'Включить в отгрузку',
    message: (count: number) => `Честный Знак подтвердил нанесение. Ввод в оборот ещё ожидается. Добавить в отгрузку МойСклад выбранные марки (${count})?`,
  },
  withdrawn: {
    action: 'Включить выбывшие марки',
    single: 'Включить выбывшую марку в отгрузку',
    confirm: 'Включить выбывшие марки',
    message: (count: number) => `Честный Знак подтвердил выбытие выбранных марок (${count}). При отправке их коды будут записаны в отгрузку МойСклад. В Честном Знаке марки останутся выведенными из оборота.`,
  },
  flagged: {
    action: 'Включить в отгрузку МС',
    single: 'Включить эту марку в отгрузку МС',
    confirm: 'Включить в отгрузку МС',
    message: (count: number) => `У выбранных марок (${count}) Честный Знак указал статус «В обороте» с признаком «Выбытие или блокировка». Марки могут быть заблокированы. При отправке их коды будут записаны в отгрузку МойСклад. Статус в ЧЗ сохранится.`,
  },
}

export function IncludeShipmentMarks({ documentId, scanIds, reason, single = false, disabled = false }: {
  documentId: string
  scanIds: string[]
  reason: ShipmentInclusionReason
  single?: boolean
  disabled?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const modal = useModal()
  const warning = warnings[reason]
  if (!warning) return null
  async function include() {
    setBusy(true)
    try {
      const ok = await modal.confirm(
        warning.message(scanIds.length),
        { title: warning.action + '?', variant: 'warn', okText: warning.confirm, danger: reason !== 'applied', focusCancel: true },
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
    {single ? warning.single : `${warning.action} (${scanIds.length})`}
  </button>
}
