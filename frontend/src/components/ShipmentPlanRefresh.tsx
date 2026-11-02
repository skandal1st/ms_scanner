import { useMutation, useQueryClient } from '@tanstack/react-query'
import { documentsApi, tsdApi, type Document, type PlanSync } from '../api/client'
import { useScanStore } from '../store/scanStore'

export function ShipmentPlanRefresh({ documentId, summary, terminal = false, disabled = false, onUpdated }: {
  documentId: string; summary?: PlanSync | null; terminal?: boolean; disabled?: boolean; onUpdated?: (doc: Document) => void
}) {
  const cache = useQueryClient()
  const update = useMutation({
    mutationFn: () => (terminal ? tsdApi.refreshPlan(documentId) : documentsApi.refreshPlan(documentId)).then(r => r.data),
    onSuccess: async data => {
      if (!terminal) {
        const store = useScanStore.getState()
        if (store.document?.id === documentId) {
          store.setDocument(data)
          if (store.targetProductId && !data.plan.some(row => row.product_id === store.targetProductId)) store.setTargetProductId(null)
        }
      }
      onUpdated?.(data)
      await cache.invalidateQueries({ queryKey: [terminal ? 'tsd-document' : 'document', documentId] })
    },
  })
  const error = update.error as { response?: { data?: { detail?: string } } } | null
  return <section className={terminal ? 'tsd-alert' : 'alert'} style={{ display: 'block', ...(terminal ? {} : { margin: '12px 18px' }) }} aria-label="Пересверка плана отгрузки">
    <div className="flex-row gap-8" style={{ flexWrap: 'wrap', alignItems: 'center' }}>
      <button type="button" className={terminal ? 'tsd-button' : 'button'} disabled={disabled || update.isPending}
        aria-busy={update.isPending} onClick={() => update.mutate()}>{update.isPending ? 'Сверяем с МС…' : 'Обновить план из МС'}</button>
      <span className="hint">Если заказ и отгрузка изменились в МС. Отсканированные марки сохранятся.</span>
    </div>
    {error && <p role="alert" className="text-error">{error.response?.data?.detail || 'Не удалось обновить план. Проверьте связь и повторите попытку.'}</p>}
    {summary && <div role="status">
      <p>Сверено с отгрузкой МС: {new Date(summary.checked_at).toLocaleString('ru-RU')}.
        {summary.changes.length ? ` Изменений: ${summary.changes.length}. Проверьте позиции и проблемные марки.` : ' План актуален, изменений нет.'}</p>
      {summary.changes.length > 0 && <details open={summary.changes.length <= 5}><summary>Что изменилось</summary>
        <ul>{summary.changes.map((row, index) => <li key={`${row.product_id}-${index}`}>
          {row.product_name}: {row.kind === 'added' ? `добавлено ${row.after} шт.` : row.kind === 'removed'
            ? `удалено из плана (было ${row.before} шт.)` : row.kind === 'quantity' ? `${row.before} → ${row.after} шт.` : 'обновлены сведения о товаре'}
        </li>)}</ul>
      </details>}
    </div>}
  </section>
}
