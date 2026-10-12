import type { PlanProgress } from '../store/scanStore'

const names = new Intl.Collator('ru', { sensitivity: 'base', numeric: true })

export function sortShipmentProgress(progress: PlanProgress): PlanProgress {
  const compare = (a: { product_name: string; folder_name?: string | null }, b: { product_name: string; folder_name?: string | null }) => {
    const left = a.folder_name?.trim() || ''
    const right = b.folder_name?.trim() || ''
    if (Boolean(left) !== Boolean(right)) return left ? -1 : 1
    return names.compare(left, right) || names.compare(a.product_name.trim(), b.product_name.trim())
  }
  return {
    ...progress,
    rows: [...progress.rows].sort(compare),
    offPlanRows: [...progress.offPlanRows].sort(compare),
  }
}
