import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ScanInput } from '../components/ScanInput'
import { CodesTable } from '../components/CodesTable'
import { StatsPanel } from '../components/StatsPanel'
import { DocumentSelector } from '../components/DocumentSelector'
import { CustomerOrderPicker } from '../components/CustomerOrderPicker'
import { ProgressTable } from '../components/ProgressTable'
import { FlowWorkspaceTabs, useFlowWorkspaceTab } from '../components/FlowWorkspaceTabs'
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
import { setOrganizationProfileId } from '../lib/organizationProfile'
import { useScanUpdates } from '../hooks/useScanUpdates'
import { ShipmentCorrections } from '../components/ShipmentCorrections'

const TsdDocumentQr = lazy(() =>
  import('../components/TsdDocumentQr').then((module) => ({ default: module.TsdDocumentQr })),
)

interface ShipmentPageProps {
  /** Встроенный режим (попап МС): документ задан заранее, без выбора; после
   *  успешной отправки вызывается onSent (попап закрывает окно МС). */
  embedded?: boolean
  terminalMode?: boolean
  presetDocument?: Document | null
  onSent?: () => void
}

export function ShipmentPage({
  embedded = false,
  terminalMode = false,
  presetDocument = null,
  onSent,
}: ShipmentPageProps = {}) {
  const modal = useModal()
  const { document, setDocument, reset, stats, scans, getProgress, addScan, unpackBox, czTokenExpired, setCzTokenExpired, verifying, setVerifying } = useScanStore()
  const progress = getProgress()
  useScanUpdates(terminalMode ? document?.id ?? null : null)
  const [workspaceTab, setWorkspaceTab] = useFlowWorkspaceTab('shipment_workspace_tab')
  const workspaceIssueCount = stats.invalid + stats.duplicate + stats.unknown_product + stats.used_in_other_doc
  const summaryCount = progress.hasPlan
    ? `${progress.total.scanned}/${progress.total.expected}`
    : progress.total.addedTotal
  // Не закрываем вкладку после отгрузки — иначе закрылся бы и COM-порт
  // (churn open/close на каждую отгрузку «залипляет» виртуальный порт). Держим
  // вкладку и порт открытыми всю смену, как это делает 1С.
  const [pendingDoc, setPendingDoc] = useState<Document | null>(null)
  const [showConfirm, setShowConfirm] = useState(false)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkBusy, setBulkBusy] = useState(false)
  const openingRef = useRef(false)
  const [opening, setOpening] = useState(false)
  const [correctionsOpen, setCorrectionsOpen] = useState(false)
  const collectionReady = Boolean(document && (!document.moysklad_id || document.collection_started))
  const {
    send: sendToMs,
    sending,
    progressLabel,
    done,
    closingTab,
    error: sendError,
    setError: setSendError,
    reset: resetSend,
  } = useSendToMoysklad<Document>({
    activeDocument: document,
    fetchDoc: (id) => documentsApi.get(id),
    onPoll: (fresh) => setDocument(fresh),
    // В попапе окно закрывает не window.close, а ClosePopup через onSent.
    autoCloseTab: false,
  })

  const handleBulkMarks = async (codes: string[]) => {
    if (!document || !collectionReady) return
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

  // Попап МС: документ приходит извне — выбираем его сразу, без DocumentSelector.
  useEffect(() => {
    if (embedded && presetDocument) {
      setPendingDoc(presetDocument)
      setDocument(presetDocument)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedded, presetDocument?.id])

  // Внешняя вкладка COM-сканирования, открытая из кнопки МС: ?doc=<id> — сразу
  // выбираем этот документ (DocumentSelector остаётся для перехода к следующему,
  // не переоткрывая вкладку/порт). Один раз на монтировании.
  const [searchParams] = useSearchParams()
  useEffect(() => {
    if (embedded) return
    const docId = searchParams.get('doc')
    if (!docId) return
    let cancelled = false
    documentsApi
      .get(docId)
      .then(({ data }) => {
        if (cancelled) return
        setOrganizationProfileId(data.organization_profile_id)
        void handleSelectDoc(data)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Попап: после успешной отправки — короткая пауза на оверлей «Отгружено», затем
  // отдаём управление попапу (он шлёт ClosePopup хост-окну МС).
  useEffect(() => {
    if (done && embedded && onSent) {
      const t = setTimeout(onSent, 1400)
      return () => clearTimeout(t)
    }
  }, [done, embedded, onSent])

  const handleSelectDoc = async (doc: Document) => {
    if (openingRef.current) return
    openingRef.current = true
    setOpening(true)
    setPendingDoc(null)
    reset()
    try {
      setPendingDoc(doc)
      setDocument(doc)
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
      modal.alert(detail || 'Не удалось открыть отгрузку.', { variant: 'error' })
    } finally {
      openingRef.current = false
      setOpening(false)
    }
  }

  const handleStartCollection = async () => {
    if (!document || openingRef.current) return
    openingRef.current = true
    setOpening(true)
    try {
      await documentsApi.startCollection(document.id)
      const { data } = await documentsApi.get(document.id)
      setDocument(data)
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
      modal.alert(detail || 'Не удалось начать сборку. Повторите попытку.', { variant: 'error' })
    } finally {
      openingRef.current = false
      setOpening(false)
    }
  }

  // Отвязаться от текущей отгрузки → вернуться к выбору (без F5). Сканы остаются в БД.
  const handleDetach = () => {
    reset()
    setPendingDoc(null)
    setShowConfirm(false)
    setShowClearConfirm(false)
  }

  // После «Отгружено» (COM-режим): убрать оверлей и вернуться к выбору документа,
  // не закрывая вкладку — COM-порт остаётся открытым для следующей отгрузки.
  const handleNextShipment = () => {
    resetSend()
    handleDetach()
  }

  // window.close() может быть запрещён браузером, а в COM-режиме вкладка намеренно
  // остаётся открытой. В обоих случаях после подтверждения возвращаем чистый выбор.
  useEffect(() => {
    if (!done || embedded) return
    const t = window.setTimeout(handleNextShipment, closingTab ? 2800 : 2400)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, embedded, closingTab])

  // Вкладка открыта из МС (window.opener) — можно вручную вернуться, закрыв её.
  const canReturnToMs = typeof window !== 'undefined' && !!window.opener

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
    document?.status === 'processing' ? 'Обрабатывается' : collectionReady ? 'В процессе' : 'Не начата'

  if (correctionsOpen) return <ShipmentCorrections onClose={() => setCorrectionsOpen(false)} />
  return (
    <div className="acc-page" style={terminalMode ? {height:'100%'} : undefined}>
      <header className="acc-header">
        <div className="flex-row gap-8" style={{ alignItems: 'center' }}>
          <h1 className="acc-header__title">Отгрузка маркировки</h1>
          {!embedded && <button type="button" className="button button--sm" onClick={() => setCorrectionsOpen(true)}>Исправить отгрузку</button>}
          {document && <span className={docStatusCls}>{docStatusText}</span>}
          {document && !embedded && (
            <button
              type="button"
              className="button button--sm"
              onClick={handleDetach}
              title="Отвязаться и выбрать другую отгрузку (сканы остаются в документе)"
            >
              <Icon name="close" size={14} /> Отвязаться
            </button>
          )}
          {document?.moysklad_id && (!embedded || terminalMode) ? (
            <Suspense fallback={null}>
              <TsdDocumentQr moyskladId={document.moysklad_id} name={document.name} />
            </Suspense>
          ) : null}
        </div>
        <span className="acc-header__doc">
          {document?.display_name || document?.name || 'Документ не выбран'}
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

      {document?.status === 'draft' && !collectionReady && (
        <div className="alert" style={{ margin: '12px 18px 0', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="alert__spacer">Проверьте план отгрузки и начните сборку.</span>
          <button type="button" className="button button--primary" disabled={opening} onClick={handleStartCollection}>
            {opening ? 'Начинаем сборку…' : 'Начать сборку'}
          </button>
        </div>
      )}
      {terminalMode && <div style={{padding:'12px 18px'}}>
        <p className="hint">Режим ТСД: сканируйте марки на терминале. Изменения сборки отображаются здесь автоматически.</p>
        <StatsPanel />
      </div>}
      <div className="acc-body">
        {!terminalMode && <>
        <div className="acc-left" style={{ width: leftWidth }}>
          {!embedded && (
            <>
              <CustomerOrderPicker onSelect={handleSelectDoc} disabled={opening || sending || bulkBusy || document?.status === 'processing'} />
              <fieldset disabled={opening} style={{border: 0, padding: 0, margin: 0, minWidth: 0}}>
                <DocumentSelector kind="demand" onSelect={handleSelectDoc} selected={document} />
              </fieldset>
              {opening && <p role="status" className="hint">Подождите…</p>}
            </>
          )}
          <ManualProductTargetBar />
          <ScanInput documentId={collectionReady ? document?.id ?? null : null}
            inactiveHint={document ? 'Нажмите «Начать сборку», чтобы сканировать марки' : undefined} />
          <button
            type="button"
            className="button"
            style={{ marginTop: 8, width: '100%', justifyContent: 'center' }}
            disabled={!collectionReady}
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
        </>}

        <div className="acc-right">
          <UnknownProductsPicker />
          <FlowWorkspaceTabs
            summaryLabel="Сборка"
            summaryCount={summaryCount}
            marksCount={scans.length}
            issueCount={workspaceIssueCount}
            pendingCount={stats.scanned + stats.pending}
            active={workspaceTab}
            onChange={setWorkspaceTab}
            summary={
              progress.hasSummary ? (
                <ProgressTable tabbed sortByName showScanTarget={!terminalMode} onInspectMarks={() => setWorkspaceTab('marks')} />
              ) : (
                <div className="flow-tabs__empty">
                  Выберите отгрузку и начните сканирование — здесь появится состав сборки.
                </div>
              )
            }
            marks={
              <div className="acc-table-wrap">
                <CodesTable signatureInn={signatureInn} />
              </div>
            }
          />
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
            disabled={!document || document.status !== 'draft' || sending || verifying}
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
            !collectionReady ||
            scans.length === 0 ||
            sending ||
            document?.status === "processing" ||
            document?.status === "accepted" ||
            verifying ||
            stats.scanned > 0 ||
            stats.pending > 0 ||
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
            ? progressLabel
            : stats.scanned > 0
              ? `Проверьте марки (${stats.scanned})`
              : stats.unknown_product > 0
                ? `Сопоставьте товары (${stats.unknown_product})`
                : terminalMode ? `Отправить в МС (${progress.hasPlan ? `${progress.total.scanned}/${progress.total.expected}` : progress.total.addedTotal})`
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

      {done && (
        <div className="done-overlay">
          <div className="done-overlay__card">
            <div className="done-overlay__check">
              <Icon name="check" size={32} />
            </div>
            <div className="done-overlay__title">Отгружено</div>
            {/* Вкладка из МС закроется сама; в обычной вкладке автозакрытия нет —
                даём явное подтверждение и кнопку, иначе кладовщик не видит успех
                и жмёт «Отгрузить» снова (марки уже записаны, идёт бесконечный повтор). */}
            <div className="done-overlay__sub">
              {closingTab || embedded
                ? 'Возвращаемся в МойСклад…'
                : 'Марки записаны. Можно сканировать следующую отгрузку.'}
            </div>
            {!closingTab && !embedded && (
              <div className="flex-row gap-8" style={{ marginTop: 16, justifyContent: 'center' }}>
                <button
                  type="button"
                  className="button button--success"
                  onClick={handleNextShipment}
                >
                  Следующая отгрузка
                </button>
                {canReturnToMs && (
                  <button type="button" className="button" onClick={() => window.close()}>
                    Вернуться в МойСклад
                  </button>
                )}
              </div>
            )}
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
