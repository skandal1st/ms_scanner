import { useDeferredValue, useState } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { documentsApi } from '../api/client'
import type { Document, MsDocument } from '../api/client'
import { getOrganizationProfileId } from '../lib/organizationProfile'

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : 'Не удалось открыть заказ. Повторите попытку.'
}

export function CustomerOrderPicker({ onSelect, disabled }: { onSelect: (doc: Document) => void; disabled?: boolean }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [choice, setChoice] = useState<{ order: MsDocument; shipments: MsDocument[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const orders = useInfiniteQuery({
    queryKey: ['customer-orders', getOrganizationProfileId(), deferredSearch], enabled: open,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => documentsApi.customerOrders(deferredSearch, pageParam).then(r => r.data),
    getNextPageParam: (last, pages) => last.length === 50 ? pages.length * 50 : undefined,
  })
  const resolve = useMutation({
    networkMode: 'always',
    mutationFn: ({ shipment, order }: { shipment: MsDocument; order: MsDocument }) =>
      documentsApi.resolve(shipment.id, 'demand', order.id).then(r => r.data),
    onSuccess: (doc) => { onSelect(doc); setOpen(false); setChoice(null); setError(null); void qc.invalidateQueries({ queryKey: ['documents'] }) },
    onError: (err) => setError(errorMessage(err)),
  })
  const choose = useMutation({
    networkMode: 'always',
    mutationFn: (order: MsDocument) => documentsApi.orderShipments(order.id).then(r => ({ order, shipments: r.data })),
    onMutate: () => setError(null),
    onSuccess: (result) => {
      if (result.shipments.length === 1) resolve.mutate({ order: result.order, shipment: result.shipments[0] })
      else setChoice(result)
    },
    onError: (err) => setError(errorMessage(err)),
  })
  const busy = Boolean(disabled || choose.isPending || resolve.isPending)
  return <section className="customer-order-picker">
    <button type="button" className="button button--sm" disabled={busy} aria-expanded={open}
      onClick={() => { setOpen(v => !v); setChoice(null); setError(null) }}>{open ? 'Закрыть выбор заказа' : 'Собрать из заказа покупателя'}</button>
    {open && <div className="customer-order-picker__body">
      <h3>{choice ? `Заказ ${choice.order.name}: отгрузки` : 'Заказы покупателей'}</h3>
      {choice ? <>
        <button type="button" className="button button--sm" disabled={busy} onClick={() => { setChoice(null); setError(null) }}>К списку заказов</button>
        {choice.shipments.length === 0 && <p className="hint">{choice.order.empty_shipments_message || 'Нет доступных отгрузок. Проверьте связанные документы в МойСкладе.'}</p>}
        <div className="doc-list">{choice.shipments.map(shipment => <button key={shipment.id} type="button" className="doc-list__item" disabled={busy}
          onClick={() => { setError(null); resolve.mutate({ shipment, order: choice.order }) }}>
          <span>Отгрузка {shipment.name}</span><span className="doc-list__item-count">{shipment.agent_name || 'Открыть'}</span>
        </button>)}</div>
        <button type="button" className="button button--sm" disabled={busy} onClick={() => choose.mutate(choice.order)}>Обновить отгрузки</button>
      </> : <>
        <input className="ui-input ui-input--block" aria-label="Поиск заказа покупателя" placeholder="Номер заказа или контрагент"
          value={search} disabled={busy} onChange={event => setSearch(event.target.value)} />
        {orders.isLoading && <p role="status" className="hint">Загружаем заказы…</p>}
        {orders.error && <div className="alert alert--error">{errorMessage(orders.error)} <button type="button" className="button button--sm" onClick={() => orders.refetch()}>Повторить</button></div>}
        {!orders.isLoading && !orders.error && orders.data?.pages.flat().length === 0 && <p className="hint">Заказы не найдены.</p>}
        <div className="doc-list">{orders.data?.pages.flat().map(order => <button key={order.id} type="button" className="doc-list__item" disabled={busy}
          onClick={() => choose.mutate(order)}><span>Заказ {order.name}<small style={{ display: 'block' }}>{order.agent_name || 'Контрагент не указан'}</small></span><span className="doc-list__item-count">{order.shipment_count === null ? 'Проверить отгрузки' : order.shipment_count ? `Отгрузок: ${order.shipment_count}` : order.retail_sale_count ? 'Розничная продажа' : order.shipment_count === 0 ? 'Нет отгрузки' : 'Открыть'}</span></button>)}</div>
        {orders.hasNextPage && <button type="button" className="button button--sm" disabled={busy || orders.isFetchingNextPage} onClick={() => orders.fetchNextPage()}>Показать ещё заказы</button>}
      </>}
      {(choose.isPending || resolve.isPending) && <p className="hint" role="status">Открываем отгрузку…</p>}
      {error && <div className="alert alert--error" role="alert">{error}</div>}
    </div>}
  </section>
}
