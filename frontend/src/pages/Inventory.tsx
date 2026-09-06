import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import {
  inventoryApi,
  type InventoryStore,
  type SnapshotStatus,
  type ReconcileResult,
  type ReconcileDiff,
} from '../api/client'

const nf = (n: number) => n.toLocaleString('ru')
const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString('ru') : '—')

const DIFF_TABS: { value: ReconcileDiff; label: string }[] = [
  { value: 'to_search', label: 'Искать (ЧЗ−УПД−МС)' },
  { value: 'cz_gt_ms', label: 'ЧЗ > МС (сырое)' },
  { value: 'ms_gt_cz', label: 'МС > ЧЗ' },
  { value: 'mismatch', label: 'Все расхождения' },
  { value: 'all', label: 'Всё' },
]

export function InventoryPage() {
  const [stores, setStores] = useState<InventoryStore[]>([])
  const [storesAvailable, setStoresAvailable] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [storesDirty, setStoresDirty] = useState(false)
  const [savingStores, setSavingStores] = useState(false)

  const [cz, setCz] = useState<SnapshotStatus | null>(null)
  const [ms, setMs] = useState<SnapshotStatus | null>(null)

  const [recon, setRecon] = useState<ReconcileResult | null>(null)
  const [reconLoading, setReconLoading] = useState(false)
  const [brand, setBrand] = useState<string>('')
  const [diff, setDiff] = useState<ReconcileDiff>('to_search')

  const [err, setErr] = useState<string | null>(null)

  const loadStores = async () => {
    try {
      const r = await inventoryApi.stores()
      setStores(r.data.stores)
      setStoresAvailable(r.data.available)
      setSelected(new Set(r.data.selected))
      setStoresDirty(false)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось получить склады МойСклад')
    }
  }

  const loadCz = async () => {
    try {
      const r = await inventoryApi.czStatus()
      setCz(r.data)
      return r.data.running
    } catch {
      return false
    }
  }
  const loadMs = async () => {
    try {
      const r = await inventoryApi.msStatus()
      setMs(r.data)
      return r.data.running
    } catch {
      return false
    }
  }

  const loadRecon = async (b = brand, d = diff) => {
    setReconLoading(true)
    try {
      const r = await inventoryApi.reconcile(b, d)
      setRecon(r.data)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось выполнить сверку')
    } finally {
      setReconLoading(false)
    }
  }

  useEffect(() => {
    loadStores()
    loadCz()
    loadMs()
    loadRecon()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Поллинг снимков, пока идёт обновление; по завершении — пересверка.
  useEffect(() => {
    if (!cz?.running && !ms?.running) return
    const t = setInterval(async () => {
      const [czRun, msRun] = await Promise.all([loadCz(), loadMs()])
      if (!czRun && !msRun) {
        clearInterval(t)
        loadRecon()
      }
    }, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cz?.running, ms?.running])

  const toggleStore = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
    setStoresDirty(true)
  }

  const saveStores = async () => {
    setSavingStores(true)
    setErr(null)
    try {
      await inventoryApi.saveStores([...selected])
      setStoresDirty(false)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось сохранить склады')
    } finally {
      setSavingStores(false)
    }
  }

  const refreshCz = async () => {
    setErr(null)
    try {
      await inventoryApi.czRefresh()
      setCz((s) => (s ? { ...s, running: true } : { running: true, size: 0, at: null, result: null }))
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось запустить обновление остатка ЧЗ')
    }
  }
  const refreshMs = async () => {
    setErr(null)
    try {
      await inventoryApi.msRefresh()
      setMs((s) => (s ? { ...s, running: true } : { running: true, size: 0, at: null, result: null }))
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось запустить обновление остатка МС')
    }
  }

  const selectBrand = (b: string) => {
    setBrand(b)
    loadRecon(b, diff)
  }
  const selectDiff = (d: ReconcileDiff) => {
    setDiff(d)
    loadRecon(brand, d)
  }

  const exportXlsx = async () => {
    try {
      const r = await inventoryApi.reconcileXlsx(brand, diff)
      const url = URL.createObjectURL(r.data as Blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `Сверка ЧЗ-МС ${new Date().toLocaleDateString('ru')}.xlsx`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Не удалось выгрузить XLSX')
    }
  }

  const hasMs = Boolean(recon?.has_ms_snapshot)
  const czErr = cz?.result?.error as string | undefined
  const msErr = ms?.result?.error as string | undefined

  return (
    <div className="mc">
      <header className="mc-head">
        <div>
          <h1 className="mc-head__title">Инвентаризация</h1>
          <p className="mc-head__sub">
            Сверка остатка марок в Честном Знаке с учётным остатком МойСклада по номенклатуре.
            «Искать» = ЧЗ&nbsp;−&nbsp;УПД&nbsp;−&nbsp;МС: из числящегося за нами в ЧЗ вычитаем и полку
            (МС), и уже отгруженное по УПД, но не принятое покупателем (в пути) — остаётся то, что
            реально надо искать. Инвентаризацию удобно вести по одному бренду (группе товаров).
          </p>
        </div>
        {hasMs && (
          <div className="mc-head__actions">
            <button className="button" onClick={exportXlsx}>
              <Icon name="upload" size={15} />
              Экспорт XLSX
            </button>
            <button className="button" onClick={() => loadRecon()} disabled={reconLoading}>
              <Icon name="refresh" size={15} />
              {reconLoading ? 'Сверка…' : 'Пересверить'}
            </button>
          </div>
        )}
      </header>

      {err && (
        <div className="alert alert--error" style={{ marginTop: 16 }}>
          {err}
          <span className="alert__spacer" />
          <button className="button button--sm" onClick={() => setErr(null)}>Закрыть</button>
        </div>
      )}

      {/* Два снимка */}
      <section className="mc-stats" style={{ marginTop: 18 }}>
        <div className="card" style={{ flex: 1, padding: 18 }}>
          <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 className="mc-tool__title" style={{ margin: 0 }}>Остаток ЧЗ</h2>
            <button className="button button--sm" onClick={refreshCz} disabled={cz?.running}>
              {cz?.running ? 'Обновление…' : 'Обновить'}
            </button>
          </div>
          <div className="mc-run-result" style={{ marginTop: 10 }}>
            Марок: <b>{cz ? nf(cz.size) : '—'}</b> · снимок от {fmtDate(cz?.at ?? null)}
          </div>
          {czErr && <div className="alert alert--warn" style={{ marginTop: 8 }}>{czErr}</div>}
        </div>
        <div className="card" style={{ flex: 1, padding: 18 }}>
          <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 className="mc-tool__title" style={{ margin: 0 }}>Остаток МС</h2>
            <button className="button button--sm" onClick={refreshMs} disabled={ms?.running}>
              {ms?.running ? 'Обновление…' : 'Обновить'}
            </button>
          </div>
          <div className="mc-run-result" style={{ marginTop: 10 }}>
            Позиций: <b>{ms ? nf(ms.size) : '—'}</b> · снимок от {fmtDate(ms?.at ?? null)}
          </div>
          {msErr && <div className="alert alert--warn" style={{ marginTop: 8 }}>{msErr}</div>}
        </div>
      </section>

      {/* Наши склады */}
      <details className="mc-tools" style={{ marginTop: 16 }}>
        <summary>
          <Icon name="chevron" size={16} className="mc-tools__chev" />
          Наши склады ({!storesAvailable ? 'все склады' : selected.size ? `${selected.size} выбрано` : 'все склады'})
        </summary>
        <div className="mc-tools__body">
          <div className="mc-tool">
            {!storesAvailable ? (
              <div className="alert alert--warn">
                Выбор складов недоступен — у приложения нет права на список складов МойСклад.
                Остаток МС берётся по всем складам. Чтобы включить выбор по юрлицу, нужно добавить
                право на склады в дескриптор решения и переустановить его.
              </div>
            ) : (
            <p className="mc-tool__desc">
              Остаток МС берётся по выбранным складам (периметр вашего юрлица). Ничего не выбрано —
              учитываются все склады. После изменения обновите остаток МС.
            </p>
            )}
            <div className="flex-row" style={{ flexWrap: 'wrap', gap: 10 }}>
              {stores.map((s) => (
                <label key={s.id} className="flex-row gap-8" style={{ alignItems: 'center', minWidth: 200 }}>
                  <input type="checkbox" checked={selected.has(s.id)} onChange={() => toggleStore(s.id)} />
                  <span>{s.name}</span>
                </label>
              ))}
              {storesAvailable && stores.length === 0 && <span className="text-muted">Склады не загружены.</span>}
            </div>
            {storesDirty && (
              <div className="mc-form" style={{ marginTop: 12 }}>
                <button className="button button--primary" onClick={saveStores} disabled={savingStores}>
                  {savingStores ? 'Сохранение…' : 'Сохранить склады'}
                </button>
              </div>
            )}
          </div>
        </div>
      </details>

      {/* Сверка */}
      <section className="mc-report" style={{ marginTop: 16 }}>
        <div className="mc-report__bar">
          <h2 className="mc-report__title">Расхождения ЧЗ ↔ МС</h2>
          <div className="seg" role="tablist">
            {DIFF_TABS.map((t) => (
              <button
                key={t.value}
                className={`seg__btn${diff === t.value ? ' is-active' : ''}`}
                onClick={() => selectDiff(t.value)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {!recon ? (
          <div className="mc-empty">{reconLoading ? 'Сверка…' : 'Нет данных.'}</div>
        ) : !hasMs ? (
          <div style={{ padding: '0 20px 20px' }}>
            <div className="alert alert--warn">
              Нет снимка остатка МС. Обновите остаток ЧЗ и остаток МС — после выгрузки появится
              таблица расхождений.
            </div>
          </div>
        ) : (
          <>
            <div className="mc-stats">
              <div className="mc-stat">
                <div className="mc-stat__num">{nf(recon.totals.positions)}</div>
                <div className="mc-stat__label">позиций</div>
              </div>
              <div className="mc-stat">
                <div className="mc-stat__num">{nf(recon.totals.qty_cz)}</div>
                <div className="mc-stat__label">марок в ЧЗ</div>
              </div>
              <div className="mc-stat">
                <div className="mc-stat__num">{nf(recon.totals.qty_upd)}</div>
                <div className="mc-stat__label">в пути (УПД)</div>
              </div>
              <div className="mc-stat">
                <div className="mc-stat__num">{nf(recon.totals.qty_ms)}</div>
                <div className="mc-stat__label">учтено в МС</div>
              </div>
              <div className="mc-stat">
                <div className={`mc-stat__num${recon.search_total > 0 ? ' mc-stat__num--alert' : ''}`}>
                  {nf(recon.search_total)}
                </div>
                <div className="mc-stat__label">нужно искать</div>
              </div>
            </div>

            {/* Фильтр по бренду */}
            <div className="mc-form" style={{ padding: '0 20px 4px' }}>
              <div className="mc-form__field" style={{ minWidth: 260 }}>
                <label className="field-label">Бренд (группа товаров)</label>
                <select className="ui-input" value={brand} onChange={(e) => selectBrand(e.target.value)}>
                  <option value="">Все бренды</option>
                  {recon.brands.map((b) => (
                    <option key={b.folder_id || b.folder_name} value={b.folder_id || b.folder_name}>
                      {b.folder_name} · искать {b.to_search}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="mc-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Бренд</th>
                    <th>Товар</th>
                    <th>GTIN</th>
                    <th className="mc-num">ЧЗ</th>
                    <th className="mc-num">УПД</th>
                    <th className="mc-num">МС</th>
                    <th className="mc-num">Δ</th>
                    <th className="mc-num">Искать</th>
                  </tr>
                </thead>
                <tbody>
                  {recon.rows.map((r, i) => (
                    <tr key={(r.gtin || '') + i}>
                      <td className="mc-state">{r.folder_name}</td>
                      <td style={{ fontWeight: 500 }}>{r.product_name || '—'}</td>
                      <td className="tabular">{r.gtin || '—'}</td>
                      <td className="mc-num">{nf(r.qty_cz)}</td>
                      <td className="mc-num">{nf(r.qty_upd)}</td>
                      <td className="mc-num">{nf(r.qty_ms)}</td>
                      <td className={r.diff !== 0 ? 'mc-num--alert' : 'mc-num'}>
                        {r.diff > 0 ? '+' : ''}{nf(r.diff)}
                      </td>
                      <td className={r.to_search > 0 ? 'mc-num--alert' : 'mc-num'} style={{ fontWeight: 600 }}>
                        {nf(r.to_search)}
                      </td>
                    </tr>
                  ))}
                  {recon.rows.length === 0 && (
                    <tr><td colSpan={8} className="text-muted" style={{ padding: 20 }}>Ничего не найдено по фильтру.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
