import type { PlanProgress } from '../store/scanStore'

const names = new Intl.Collator('ru', { sensitivity: 'base', numeric: true })

export function sortShipmentProgress(progress: PlanProgress): PlanProgress {
  const compare = (a: { product_name: string }, b: { product_name: string }) =>
    names.compare(a.product_name.trim(), b.product_name.trim())
  return {
    ...progress,
    rows: [...progress.rows].sort(compare),
    offPlanRows: [...progress.offPlanRows].sort(compare),
  }
}
