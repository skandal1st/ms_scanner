import type { CustomerOrderFilter } from '../api/client'

export function CustomerOrderFilterSelect({ filters, value, onChange, disabled, tsd = false }: {
  filters: CustomerOrderFilter[]; value: string; onChange: (id: string) => void; disabled?: boolean; tsd?: boolean
}) {
  return <label className={`customer-order-filter-select${tsd ? ' customer-order-filter-select--tsd' : ''}`}>
    <span>Фильтр заказов</span>
    <select className="ui-input ui-input--block" aria-label="Фильтр заказов" value={value} disabled={disabled} onChange={e => onChange(e.target.value)}>
      <option value="">Все заказы</option>
      {filters.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>
  </label>
}
