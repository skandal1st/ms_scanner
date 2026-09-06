import { useState } from 'react'
import { ScanInput } from '../components/ScanInput'
import { CodesTable } from '../components/CodesTable'
import { StatsPanel } from '../components/StatsPanel'
import { DocumentSelector } from '../components/DocumentSelector'
import { ProgressTable } from '../components/ProgressTable'
import { ManualProductTargetBar } from '../components/ManualProductTargetBar'
import { UnknownProductsPicker } from '../components/UnknownProductsPicker'
import { BulkMarksModal } from '../components/BulkMarksModal'
import { Icon } from '../components/Icon'
import { useModal } from '../components/ModalProvider'
import { useScanStore, ownerCheckState } from '../store/scanStore'
import { useLoadDocument, useClearDocumentScans, useIntegration } from '../hooks/useDocuments'
import { useResizableWidth } from '../hooks/useResizableWidth'
import { useSendToMoysklad } from '../hooks/useSendToMoysklad'
import { scansApi, documentsApi } from '../api/client'
import type { Document } from '../api/client'

export function ShipmentPage() {
  const modal = useModal()
  const { document, setDocument, reset, stats, scans, getProgress, addScan, unpackBox, czTokenExpired, setCzTokenExpired, verifying, setVerifying } = useScanStore()
  const progress = getProgress()
  const [pendingDoc, setPendingDoc] = useState<Document | null>(null)
  const [showConfirm, setShowConfirm] = useState(false)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkBusy, setBulkBusy] = useState(false)
  const {
    send: sendToMs,
    sending,
    error: sendError,
    closingTab,
    setError: setSendError,
  } = useSendToMoysklad<Document>({
    fetchDoc: (id) => documentsApi.get(id),
    onPoll: (fresh) => setDocument(fresh),
  })

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
      modal.alert('Не удалось выгрузить XLSX. Попробуйте ещё раз.', { variant: 'error' })
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
    setShowConfirm(false)
    await sendToMs(document.id)
  }

  // Пакетная проверка марок в ЧЗ (основной флоу: скан — локально, проверка — здесь).
  // Завершение придёт по WS (verify_done) → setVerifying(false). Прогресс по каждой
  // марке — через scan_update, статусы обновятся в таблице сами.
  const handleVerify = async () => {
    if (!document || verifying) return
    setVerifying(true)
    try {
      await documentsApi.verify(document.id)
    } catch (err) {
      setVerifying(false)
      console.error('Verify error:', err)
      modal.alert('Не удалось запустить проверку марок. Попробуйте ещё раз.', { variant: 'error' })
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
              className="button button--sm"
              onClick={handleDetach}
              title="Отвязаться и выбрать другую отгрузку (сканы остаются в документе)"
            >
              <Icon name="close" size={14} /> Отвязаться
            </button>
          )}
        </div>
        <span className="acc-header__doc">
          {document?.name ?? 'Документ не выбран'}
        </span>
      </header>

      {czTokenExpired && (
        <div role="alert" className="alert alert--error" style={{ margin: '12px 18px 0' }}>
          <span className="alert__spacer">
            Войдите в Честный Знак — без авторизации коды не распознаются (блоки и
            короба не разворачиваются).
          </span>
          <a href="/settings" className="button button--sm" style={{ whiteSpace: 'nowrap' }}>
            Войти в ЧЗ
          </a>
          <button
            type="button"
            className="button button--sm"
            onClick={() => setCzTokenExpired(false)}
            aria-label="Скрыть"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      )}

      {sendError && (
        <div role="alert" className="alert alert--error" style={{ margin: '12px 18px 0' }}>
          <span className="alert__spacer">{sendError}</span>
          <button
            type="button"
            className="button button--sm"
            onClick={() => setSendError(null)}
            aria-label="Скрыть"
          >
            <Icon name="close" size={14} />
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
            style={{ marginTop: 8, width: '100%', justifyContent: 'center' }}
            disabled={!document}
            onClick={() => setBulkOpen(true)}
          >
            <Icon name="upload" size={16} /> Загрузить список марок
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
          {exporting ? (
            'Выгрузка…'
          ) : (
            <>
              <Icon name="upload" size={15} style={{ transform: 'rotate(180deg)' }} /> Выгрузить в XLSX
            </>
          )}
        </button>
        <div className="acc-footer__spacer" />
        {withdrawnCount > 0 && (
          <span
            style={{ marginRight: 12, fontSize: 12, color: 'var(--st-err-fg)', whiteSpace: 'nowrap', fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: 5 }}
            title="Эти марки выведены из оборота / заблокированы. Отгрузку это не блокирует, но требует подтверждения."
          >
            <Icon name="warning" size={14} /> {withdrawnCount} выведены из оборота
          </span>
        )}
        {(ownerWarnings.mismatch > 0 || ownerWarnings.unknown > 0) && (
          <span
            style={{ marginRight: 12, fontSize: 12, color: 'var(--st-warn-fg)', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 5 }}
            title="Владельца этих марок стоит проверить. Отгрузку это не блокирует."
          >
            <Icon name="warning" size={14} />{' '}
            {ownerWarnings.mismatch > 0 && `${ownerWarnings.mismatch} с чужим владельцем`}
            {ownerWarnings.mismatch > 0 && ownerWarnings.unknown > 0 && ' · '}
            {ownerWarnings.unknown > 0 && `${ownerWarnings.unknown} не проверено`}
          </span>
        )}
        {stats.scanned > 0 && (
          <button
            type="button"
            className="button"
            disabled={!document || verifying}
            onClick={handleVerify}
            style={{ marginRight: 8 }}
          >
            {verifying
              ? 'Проверяю марки…'
              : `Проверить марки (${stats.scanned})`}
          </button>
        )}
        <button
          type="button"
          className="button button--success"
          disabled={
            !document ||
            scans.length === 0 ||
            sending ||
            verifying ||
            stats.scanned > 0 ||
            stats.unknown_product > 0
          }
          title={
            stats.scanned > 0
              ? `Сначала проверьте марки (${stats.scanned} не проверено)`
              : stats.unknown_product > 0
                ? `Сначала сопоставьте товары для ${stats.unknown_product} кодов`
                : undefined
          }
          onClick={() => setShowConfirm(true)}
        >
          {sending
            ? 'Обрабатывается…'
            : stats.scanned > 0
              ? `Проверьте марки (${stats.scanned})`
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
        <div className="done-overlay">
          <div className="done-overlay__card">
            <div className="done-overlay__check">
              <Icon name="check" size={32} />
            </div>
            <div className="done-overlay__title">Отгружено</div>
            <div className="done-overlay__sub">Возвращаемся в МойСклад…</div>
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
                <div className="alert alert--error" style={{ marginTop: 10, fontWeight: 600 }}>
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
                <div className="alert alert--warn" style={{ marginTop: 10 }}>
                  Сборка не завершена: {progress.total.scanned} из {progress.total.expected}.
                  Будет отгружена только собранная часть.
                </div>
              )}
              {progress.offPlanRows.length > 0 && (
                <div className="alert alert--error" style={{ marginTop: 10, fontWeight: 600 }}>
                  Есть {progress.offPlanRows.length}{' '}
                  {progress.offPlanRows.length === 1 ? 'позиция' : 'позиции(й)'} не из плана —
                  они тоже уйдут в отгрузку. Удалите их в блоке «Не входят в план», если это ошибка.
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
                className="button button--danger"
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
