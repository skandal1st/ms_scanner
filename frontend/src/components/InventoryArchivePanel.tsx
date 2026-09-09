import { useEffect, useState } from 'react'
import { inventoryApi, type InventoryArchivedItem } from '../api/client'

const nf = (n: number) => n.toLocaleString('ru')

/**
 * Список архивных позиций инвентаризации — GTIN, которые нельзя сопоставить с товаром МС
 * (карточка удалена/архивна) и которые кладовщик отложил из подбора. Отсюда их можно
 * вернуть обратно в подбор/не сопоставленные кнопкой «Вернуть».
 */
export function InventoryArchivePanel({
  onClose,
  onRestored,
}: {
  onClose: () => void
  onRestored?: () => void
}) {
  const [items, setItems] = useState<InventoryArchivedItem[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<Record<string, boolean>>({})

  const load = () => {
    setLoading(true)
    inventoryApi
      .archived()
      .then((r) => setItems(r.data.items))
      .catch((e: any) => setErr(e?.response?.data?.detail || 'Не удалось получить архив'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const restore = async (it: InventoryArchivedItem) => {
    const key = it.gtin || ''
    setBusy((b) => ({ ...b, [key]: true }))
    setErr(null)
    try {
      await inventoryApi.unarchive(it.gtin || '')
      setItems((list) => list.filter((x) => (x.gtin || '') !== key))
      onRestored?.()
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось вернуть из архива')
    } finally {
      setBusy((b) => ({ ...b, [key]: false }))
    }
  }

  return (
    <div className="card" style={{ padding: 18, marginTop: 12 }}>
      <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 className="mc-tool__title" style={{ margin: 0 }}>Архивные позиции</h3>
        <button className="button button--sm" onClick={onClose}>Закрыть</button>
      </div>
      <p className="mc-tool__desc" style={{ marginTop: 8 }}>
        Позиции, которые нельзя сопоставить с товаром МС (карточка удалена или архивна) и которые
        отложили из подбора. Они не попадают в подбор и «не сопоставлено». Нажмите «Вернуть», если
        товар снова появился в МС.
      </p>

      {err && <div className="alert alert--error" style={{ marginTop: 8 }}>{err}</div>}
      {loading ? (
        <div className="mc-empty">Загрузка…</div>
      ) : items.length === 0 ? (
        <div className="mc-empty">Архив пуст.</div>
      ) : (
        <div className="mc-table-wrap" style={{ marginTop: 8 }}>
          <table className="ui-table" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th>Товар</th>
                <th>GTIN</th>
                <th>Группа</th>
                <th className="mc-num">ЧЗ</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((it, i) => {
                const key = it.gtin || ''
                return (
                  <tr key={key + i}>
                    <td style={{ fontWeight: 500 }}>{it.product_name || '—'}</td>
                    <td className="tabular">{it.gtin || '—'}</td>
                    <td className="mc-state">{it.folder_name}</td>
                    <td className="mc-num">{nf(it.qty_cz)}</td>
                    <td className="mc-num">
                      <button
                        className="button button--sm"
                        disabled={busy[key]}
                        onClick={() => restore(it)}
                      >
                        {busy[key] ? '…' : 'Вернуть'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
