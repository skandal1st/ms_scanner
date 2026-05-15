import { useState } from 'react'
import { ScanInput } from '../components/ScanInput'
import { CodesTable } from '../components/CodesTable'
import { StatsPanel } from '../components/StatsPanel'
import { DocumentSelector } from '../components/DocumentSelector'
import { ProgressTable } from '../components/ProgressTable'
import { ManualProductTargetBar } from '../components/ManualProductTargetBar'
import { useScanStore } from '../store/scanStore'
import { useLoadDocument, useProcessDocument } from '../hooks/useDocuments'
import type { Document } from '../api/client'

export function ShipmentPage() {
  const { document, setDocument, stats, scans, getProgress } = useScanStore()
  const progress = getProgress()
  const [pendingDoc, setPendingDoc] = useState<Document | null>(null)
  const [showConfirm, setShowConfirm] = useState(false)
  const [closingTab, setClosingTab] = useState(false)

  const processMutation = useProcessDocument()

  useLoadDocument(pendingDoc?.id ?? null)

  const handleSelectDoc = (doc: Document) => {
    setPendingDoc(doc)
    setDocument(doc)
  }

  const handleProcess = async () => {
    if (!document) return
    await processMutation.mutateAsync(document.id)
    setShowConfirm(false)
    if (window.opener && !window.opener.closed) {
      setClosingTab(true)
      setTimeout(() => window.close(), 1200)
    }
  }

  const hasErrors = stats.invalid > 0 || stats.duplicate > 0
  const docStatusCls =
    document?.status === 'accepted' ? 'badge badge--ok' :
    document?.status === 'processing' ? 'badge badge--info' : 'badge badge--warn'
  const docStatusText =
    document?.status === 'accepted' ? 'Завершено' :
    document?.status === 'processing' ? 'Обрабатывается' : 'В процессе'

  return (
    <div className="acc-page">
      <header className="acc-header">
        <div className="flex-row gap-8" style={{ alignItems: 'center' }}>
          <h1 className="acc-header__title">Отгрузка маркировки</h1>
          {document && <span className={docStatusCls}>{docStatusText}</span>}
        </div>
        <span className="acc-header__doc">
          {document?.name ?? 'Документ не выбран'}
        </span>
      </header>

      <div className="acc-body">
        <div className="acc-left">
          <DocumentSelector kind="demand" onSelect={handleSelectDoc} selected={document} />
          <ManualProductTargetBar />
          <ScanInput documentId={document?.id ?? null} />
          <StatsPanel />
        </div>

        <div className="acc-right">
          <ProgressTable />
          <div className="acc-right__head">
            <span className="h3" style={{ margin: 0 }}>Коды маркировки</span>
            <span className="text-muted" style={{ fontSize: 11 }}>{scans.length} шт.</span>
          </div>
          <div className="acc-table-wrap">
            <CodesTable />
          </div>
        </div>
      </div>

      <footer className="acc-footer">
        <button
          type="button"
          className="button"
          onClick={() => useScanStore.getState().reset()}
        >
          Очистить
        </button>
        <div className="acc-footer__spacer" />
        <button
          type="button"
          className="button button--success"
          disabled={!document || scans.length === 0 || processMutation.isPending}
          onClick={() => setShowConfirm(true)}
        >
          {processMutation.isPending
            ? 'Обрабатывается…'
            : progress.hasPlan
              ? stats.overflow > 0
                ? `Отгрузить ${progress.total.scanned}/${progress.total.expected} + ${stats.overflow} сверх`
                : `Отгрузить ${progress.total.scanned}/${progress.total.expected}`
              : progress.hasSummary && progress.total.addedTotal > 0
                ? `Отгрузить (${progress.total.addedTotal})`
                : 'Отгрузить товары'}
        </button>
      </footer>

      {closingTab && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(255,255,255,0.92)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            fontFamily: 'system-ui, -apple-system, sans-serif',
          }}
        >
          <div style={{ textAlign: 'center', color: '#1f2937' }}>
            <div style={{ fontSize: 18, fontWeight: 500, marginBottom: 6 }}>
              Готово ✓
            </div>
            <div style={{ fontSize: 13, color: '#6b7280' }}>
              Возвращаемся в МойСклад…
            </div>
          </div>
        </div>
      )}

      {showConfirm && (
        <div className="popup">
          <div className="popup__overlay" onClick={() => setShowConfirm(false)} />
          <dialog className="popup__body" open>
            <button
              type="button"
              className="popup__close"
              onClick={() => setShowConfirm(false)}
              aria-label="Закрыть"
            />
            <div className="popup__title">Подтверждение отгрузки</div>
            <div className="popup__content">
              <div className="settings-status">
                <div className="settings-status__row">
                  <span className="settings-status__label">Валидных:</span>
                  <span className="settings-status__value">{stats.valid}</span>
                </div>
                <div className="settings-status__row">
                  <span className="settings-status__label">Ошибок:</span>
                  <span className="settings-status__value error">{stats.invalid}</span>
                </div>
                <div className="settings-status__row">
                  <span className="settings-status__label">Дублей:</span>
                  <span className="settings-status__value" style={{ color: 'var(--ms-accent)' }}>
                    {stats.duplicate}
                  </span>
                </div>
              </div>
              {(hasErrors || stats.overflow > 0) && (
                <p className="hint">
                  В отгрузку попадут валидные ({stats.valid})
                  {stats.overflow > 0 ? ` + сверх плана (${stats.overflow})` : ''}.
                  Ошибки и дубли пропускаются.
                </p>
              )}
              {progress.hasPlan && progress.total.scanned < progress.total.expected && (
                <div
                  style={{
                    marginTop: 10,
                    padding: 10,
                    background: '#fffbeb',
                    border: '1px solid #fde68a',
                    borderRadius: 6,
                    fontSize: 12,
                    color: '#92400e',
                  }}
                >
                  Сборка не завершена: {progress.total.scanned} из {progress.total.expected}.
                  Будет отгружена только собранная часть.
                </div>
              )}
            </div>
            <div className="buttons" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
              <button type="button" className="button" onClick={() => setShowConfirm(false)}>
                Отмена
              </button>
              <button
                type="button"
                className="button button--success"
                onClick={handleProcess}
                disabled={stats.valid + stats.overflow === 0}
              >
                Отгрузить {stats.valid + stats.overflow}
                {stats.overflow > 0 ? ` (вкл. ${stats.overflow} сверх)` : ''}
              </button>
            </div>
          </dialog>
        </div>
      )}
    </div>
  )
}
