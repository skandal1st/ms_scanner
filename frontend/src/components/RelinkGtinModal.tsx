import { useEffect, useState } from 'react'
import { inventoryApi, productsApi } from '../api/client'
import type { ProductSearchItem } from '../api/client'

interface Props {
  open: boolean
  gtin: string
  currentName: string | null
  /** Товар МС, на котором GTIN сейчас (снимок остатка) — с него снимем штрихкод. */
  oldProductId: string | null
  onClose: () => void
  /** Успешная смена привязки — родитель подскажет обновить остаток МС. */
  onRelinked: () => void
}

/**
 * Модалка смены привязки GTIN: поиск товара МС по имени/артикулу/штрихкоду и кнопка
 * «Привязать сюда». Бэкенд переносит штрихкод со старой карточки на новую.
 */
export function RelinkGtinModal({ open, gtin, currentName, oldProductId, onClose, onRelinked }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ProductSearchItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setQuery('')
    setResults([])
    setError(null)
  }, [open])

  // Поиск товаров МС с дебаунсом.
  useEffect(() => {
    if (!open) return
    const q = query.trim()
    if (q.length < 2) {
      setResults([])
      return
    }
    const handle = window.setTimeout(() => {
      setLoading(true)
      productsApi
        .search(q)
        .then(({ data }) => setResults(data))
        .catch(() => setResults([]))
        .finally(() => setLoading(false))
    }, 300)
    return () => window.clearTimeout(handle)
  }, [query, open])

  if (!open) return null

  const relink = async (p: ProductSearchItem) => {
    if (saving) return
    setError(null)
    setSaving(p.id)
    try {
      await inventoryApi.relinkGtin(gtin, p.id, p.name, oldProductId)
      onRelinked()
      onClose()
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setError(ax?.response?.data?.detail ?? 'Не удалось сменить привязку')
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="popup">
      <div className="popup__overlay" onClick={saving ? undefined : onClose} />
      <dialog className="popup__body" open style={{ maxWidth: 640 }}>
        <button
          type="button"
          className="popup__close"
          onClick={onClose}
          disabled={!!saving}
          aria-label="Закрыть"
        />
        <div className="popup__title">Сменить привязку GTIN</div>
        <div className="popup__content">
          <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>
            GTIN <code className="tabular">{gtin}</code>
            {currentName ? <> · сейчас: <b>{currentName}</b></> : null}
          </div>
          <input
            autoFocus
            className="ui-input ui-input--block"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск товара МС по имени / артикулу / штрихкоду…"
          />
          {error && (
            <div className="alert alert--error" style={{ marginTop: 10 }}>
              {error}
            </div>
          )}
          <div style={{ maxHeight: 320, overflowY: 'auto', marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {loading && <div className="text-muted">Ищу…</div>}
            {!loading && query.trim().length >= 2 && results.length === 0 && (
              <div className="text-muted">Ничего не найдено.</div>
            )}
            {results.map((p) => (
              <div
                key={p.id}
                className="flex-row gap-8"
                style={{
                  alignItems: 'center',
                  padding: '8px 10px',
                  border: '1px solid var(--bd, #d0d0d0)',
                  borderRadius: 'var(--r-md, 8px)',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>{p.name}</div>
                  <div className="text-muted" style={{ fontSize: 12 }}>
                    {[p.article, p.barcodes?.[0]].filter(Boolean).join(' · ') || '—'}
                  </div>
                </div>
                <button
                  type="button"
                  className="button button--success button--sm"
                  disabled={saving !== null || p.id === oldProductId}
                  onClick={() => relink(p)}
                  title={p.id === oldProductId ? 'GTIN уже привязан к этому товару' : 'Привязать GTIN к этому товару'}
                  style={{ whiteSpace: 'nowrap' }}
                >
                  {saving === p.id ? 'Привязка…' : 'Привязать сюда'}
                </button>
              </div>
            ))}
          </div>
        </div>
        <div className="buttons" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
          <button type="button" className="button" onClick={onClose} disabled={!!saving}>
            Отмена
          </button>
        </div>
      </dialog>
    </div>
  )
}
