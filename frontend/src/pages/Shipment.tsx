import { useState } from 'react'
import { ScanInput } from '../components/ScanInput'
import { CodesTable } from '../components/CodesTable'
import { StatsPanel } from '../components/StatsPanel'
import { DocumentSelector } from '../components/DocumentSelector'
import { ProgressTable } from '../components/ProgressTable'
import { ManualProductTargetBar } from '../components/ManualProductTargetBar'
import { UnknownProductsPicker } from '../components/UnknownProductsPicker'
import { BulkMarksModal } from '../components/BulkMarksModal'
import { useScanStore, ownerCheckState } from '../store/scanStore'
import { useLoadDocument, useProcessDocument, useClearDocumentScans, useIntegration } from '../hooks/useDocuments'
import { useResizableWidth } from '../hooks/useResizableWidth'
import { scansApi, documentsApi } from '../api/client'
import type { Document } from '../api/client'

export function ShipmentPage() {
  const { document, setDocument, reset, stats, scans, getProgress, addScan, unpackBox, czTokenExpired, setCzTokenExpired } = useScanStore()
  const progress = getProgress()
  const [pendingDoc, setPendingDoc] = useState<Document | null>(null)
  const [showConfirm, setShowConfirm] = useState(false)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [closingTab, setClosingTab] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkBusy, setBulkBusy] = useState(false)

  const handleBulkMarks = async (codes: string[]) => {
    if (!document) return
    setBulkBusy(true)
    try {
      const { data: created } = await scansApi.bulk(document.id, codes, unpackBox)
      for (const s of created) addScan(s)
    } finally {
      setBulkBusy(false)
    }
  }

  const processMutation = useProcessDocument()
  const clearMutation = useClearDocumentScans()

  // Владелец подписи (ИНН из сертификата ЧЗ) — для сверки владельца марок в отгрузке.
  const { data: integration } = useIntegration()
  const signatureInn = integration?.cz_inn ?? null
  const ownerWarnings = scans.reduce(
    (acc, s) => {
      const st = ownerCheckState(s, signatureInn)
      if (st === 'mismatch') acc.mismatch += 1
      else if (st === 'unknown') acc.unknown += 1
      return acc
    },
    { mismatch: 0, unknown: 0 },
  )
  // Марки, выведенные из оборота / заблокированные (ЧЗ) — предупреждаем, но не блокируем.
  const withdrawnCount = scans.reduce((n, s) => (s.withdrawn ? n + 1 : n), 0)
  const [exporting, setExporting] = useState(false)

  const handleExportXlsx = async () => {
    if (!document) return
    setExporting(true)
    try {
      const { data } = await documentsApi.exportXlsx(document.id)
      const url = URL.createObjectURL(data)
      const a = window.document.createElement('a')
      a.href = url
      a.download = `${document.name || 'Отгрузка'}.xlsx`
      window.document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      console.error('Export XLSX error:', err)
      window.alert('Не удалось выгрузить XLSX. Попробуйте ещё раз.')
    } finally {
      setExporting(false)
    }
  }

  // Ширина левой панели (документы/сканирование) — тянется мышью за разделитель.
  const { width: leftWidth, startResize } = useResizableWidth(
    'shipment_left_width',
    440,
    { min: 300, max: 900 },
  )

  useLoadDocument(pendingDoc?.id ?? null)

  const handleSelectDoc = (doc: Document) => {
    setPendingDoc(doc)
    setDocument(doc)
  }

  // Отвязаться от текущей отгрузки → вернуться к выбору (без F5). Сканы остаются в БД.
  const handleDetach = () => {
    reset()
    setPendingDoc(null)
    setShowConfirm(false)
    setShowClearConfirm(false)
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
          {document && (
            <button
              type="button"
              className="button"
              onClick={handleDetach}
              title="Отвязаться и выбрать другую отгрузку (сканы остаются в документе)"
            >
              ✕ Отвязаться
            </button>
          )}
        </div>
        <span className="acc-header__doc">
          {document?.name ?? 'Документ не выбран'}
        </span>
      </header>

      {czTokenExpired && (
        <div
          role="alert"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            margin: '0 0 12px',
            padding: '10px 14px',
            background: '#fef2f2',
            border: '1px solid #fecaca',
            borderRadius: 8,
            color: '#b91c1c',
            fontSize: 13,
          }}
        >
          <span style={{ flex: 1 }}>
            Войдите в Честный Знак — без авторизации коды не распознаются (блоки и
            короба не разворачиваются).
          </span>
          <a href="/settings" className="button" style={{ whiteSpace: 'nowrap' }}>
            Войти в ЧЗ
          </a>
          <button
            type="button"
            className="button"
            onClick={() => setCzTokenExpired(false)}
            aria-label="Скрыть"
          >
            ✕
          </button>
        </div>
      )}

      <div className="acc-body">
        <div className="acc-left" style={{ width: leftWidth }}>
          <DocumentSelector kind="demand" onSelect={handleSelectDoc} selected={document} />
          <ManualProductTargetBar />
          <ScanInput documentId={document?.id ?? null} />
          <button
            type="button"
            className="button"
            style={{ marginTop: 8, width: '100%' }}
            disabled={!document}
            onClick={() => setBulkOpen(true)}
          >
            📋 Загрузить список марок
          </button>
          <StatsPanel />
        </div>

        <div
          className="acc-split"
          onMouseDown={startResize}
          role="separator"
          aria-orientation="vertical"
          title="Потяните, чтобы изменить ширину панелей"
        />

        <div className="acc-right">
          <UnknownProductsPicker />
          <ProgressTable />
          <div className="acc-right__head">
            <span className="h3" style={{ margin: 0 }}>Коды маркировки</span>
            <span className="text-muted" style={{ fontSize: 11 }}>{scans.length} шт.</span>
          </div>
          <div className="acc-table-wrap">
            <CodesTable signatureInn={signatureInn} />
          </div>
        </div>
      </div>

      <footer className="acc-footer">
        <button
          type="button"
          className="button"
          disabled={!document || scans.length === 0 || clearMutation.isPending}
          onClick={() => setShowClearConfirm(true)}
        >
          {clearMutation.isPending ? 'Очистка…' : 'Очистить'}
        </button>
        <button
          type="button"
          className="button"
          disabled={!document || scans.length === 0 || exporting}
          onClick={handleExportXlsx}
          title="Выгрузить структуру заказа (наименование + марка) в XLSX"
        >
          {exporting ? 'Выгрузка…' : '⬇ Выгрузить в XLSX'}
        </button>
        <div className="acc-footer__spacer" />
        {withdrawnCount > 0 && (
          <span
            style={{ marginRight: 12, fontSize: 12, color: '#b30000', whiteSpace: 'nowrap', fontWeight: 600 }}
            title="Эти марки выведены из оборота / заблокированы. Отгрузку это не блокирует, но требует подтверждения."
          >
            ⛔ {withdrawnCount} выведены из оборота
          </span>
        )}
        {(ownerWarnings.mismatch > 0 || ownerWarnings.unknown > 0) && (
          <span
            style={{ marginRight: 12, fontSize: 12, color: '#8a3008', whiteSpace: 'nowrap' }}
            title="Владельца этих марок стоит проверить. Отгрузку это не блокирует."
          >
            ⚠{' '}
            {ownerWarnings.mismatch > 0 && `${ownerWarnings.mismatch} с чужим владельцем`}
            {ownerWarnings.mismatch > 0 && ownerWarnings.unknown > 0 && ' · '}
            {ownerWarnings.unknown > 0 && `${ownerWarnings.unknown} не проверено`}
          </span>
        )}
        <button
          type="button"
          className="button button--success"
          disabled={
            !document ||
            scans.length === 0 ||
            processMutation.isPending ||
            stats.unknown_product > 0
          }
          title={
            stats.unknown_product > 0
              ? `Сначала сопоставьте товары для ${stats.unknown_product} кодов`
              : undefined
          }
          onClick={() => setShowConfirm(true)}
        >
          {processMutation.isPending
            ? 'Обрабатывается…'
            : stats.unknown_product > 0
              ? `Сопоставьте товары (${stats.unknown_product})`
              : progress.hasPlan
                ? stats.overflow > 0
                  ? `Отгрузить ${progress.total.scanned}/${progress.total.expected} + ${stats.overflow} сверх`
                  : `Отгрузить ${progress.total.scanned}/${progress.total.expected}`
                : progress.hasSummary && progress.total.addedTotal > 0
                  ? `Отгрузить (${progress.total.addedTotal})`
                  : 'Отгрузить товары'}
        </button>
      </footer>

      <BulkMarksModal
        open={bulkOpen}
        onClose={() => setBulkOpen(false)}
        onSubmit={handleBulkMarks}
        busy={bulkBusy}
      />

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
              {withdrawnCount > 0 && (
                <div
                  style={{
                    marginTop: 10,
                    padding: 10,
                    background: '#fde8e8',
                    border: '1px solid #f5b3b3',
                    borderRadius: 6,
                    fontSize: 12,
                    color: '#b30000',
                    fontWeight: 600,
                  }}
                >
                  Внимание: {withdrawnCount}{' '}
                  {withdrawnCount === 1 ? 'марка выведена' : 'марок выведены'} из оборота
                  (заблокированы). Всё равно отгрузить?
                </div>
              )}
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
                disabled={progress.total.addedTotal === 0}
              >
                Отгрузить {progress.total.addedTotal}
                {stats.overflow > 0 ? ` (вкл. ${stats.overflow} сверх)` : ''}
              </button>
            </div>
          </dialog>
        </div>
      )}

      {showClearConfirm && (
        <div className="popup">
          <div className="popup__overlay" onClick={() => setShowClearConfirm(false)} />
          <dialog className="popup__body" open>
            <button
              type="button"
              className="popup__close"
              onClick={() => setShowClearConfirm(false)}
              aria-label="Закрыть"
            />
            <div className="popup__title">Удалить все марки?</div>
            <div className="popup__content">
              <p className="hint">
                Из документа будут безвозвратно удалены все отсканированные марки ({scans.length} шт.).
                Документ останется выбранным — можно сканировать заново.
              </p>
            </div>
            <div className="buttons" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
              <button type="button" className="button" onClick={() => setShowClearConfirm(false)}>
                Отмена
              </button>
              <button
                type="button"
                className="button"
                style={{
                  color: '#ffffff',
                  border: '1px solid var(--ms-error)',
                  backgroundImage: 'none',
                  background: 'var(--ms-error)',
                }}
                disabled={clearMutation.isPending}
                onClick={async () => {
                  if (!document) return
                  await clearMutation.mutateAsync(document.id)
                  setShowClearConfirm(false)
                }}
              >
                {clearMutation.isPending ? 'Удаление…' : 'Удалить'}
              </button>
            </div>
          </dialog>
        </div>
      )}
    </div>
  )
}
