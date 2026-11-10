import { useState, useCallback, useEffect, useRef, type FormEvent } from 'react'
import { useScanner, type ScanOutcome } from '../hooks/useScanner'
import { useSerialScanner } from '../hooks/useSerialScanner'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { scansApi } from '../api/client'
import { findProgressRowForScan, useScanStore } from '../store/scanStore'
import { CodeSearchModal } from './CodeSearchModal'
import { Icon } from './Icon'

interface Props {
  documentId: string | null
  inactiveHint?: string
}

export function ScanInput({ documentId, inactiveHint = 'Выберите документ для начала сканирования' }: Props) {
  const [manualValue, setManualValue] = useState('')
  const [manualOpen, setManualOpen] = useState(false)
  const [outcome, setOutcome] = useState<ScanOutcome>()
  const [undoIds, setUndoIds] = useState<string[]>([])
  const [undoBusy, setUndoBusy] = useState(false)
  const version = useRef(0)
  const scans = useScanStore(s => s.scans)
  const verifying = useScanStore(s => s.verifying)
  useEffect(() => {
    version.current++
    setOutcome(undefined)
    setUndoIds([])
    setManualValue('')
    setManualOpen(false)
    useScanStore.getState().setDeleteMode(false)
  }, [documentId])
  const [searchOpen, setSearchOpen] = useState(false)
  const { submitCode } = useScanner(documentId)
  const deleteMode = useScanStore((s) => s.deleteMode)
  const setDeleteMode = useScanStore((s) => s.setDeleteMode)
  const unpackBox = useScanStore((s) => s.unpackBox)
  const setUnpackBox = useScanStore((s) => s.setUnpackBox)

  const handleScannedCode = useCallback(
    async (code: string) => {
      const trimmed = normalizeScannerInput(code).trim()
      if (!trimmed) return
      const request = ++version.current
      const result = await submitCode(trimmed)
      if (request !== version.current || !result) return
      setOutcome(result)
      if (!result.scan?.duplicate) setUndoIds(result.undoIds ?? [])
    },
    [submitCode],
  )

  const serial = useSerialScanner({
    enabled: true,
    onCode: handleScannedCode,
  })

  const handleManualSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const code = normalizeScannerInput(manualValue).trim()
    if (!code) return
    setManualValue('')
    await handleScannedCode(code)
  }

  const current = documentId && outcome?.documentId === documentId ? outcome : undefined
  const scan = current?.scan && scans.find(item => item.id === current.scan?.id)
  const row = scan && findProgressRowForScan(scan, useScanStore.getState().getProgress().rows)
  const resultText = (current?.scan?.duplicate || scan?.status === 'duplicate') ? 'Уже сканировали · количество прежнее'
    : scan?.status === 'invalid' || scan?.status === 'used_in_other_doc' ? scan.error_message || 'Код отклонён'
    : scan?.status === 'unknown_product' ? 'Нужно сопоставить товар'
    : scan?.status === 'pending' ? 'Идёт проверка'
    : scan?.status === 'overflow' ? 'Сверх плана'
    : scan?.status === 'valid' ? 'Принято'
    : 'Добавлено'
  const canUndo = Boolean(documentId && undoIds.length && scans.some(item =>
    item.document_id === documentId && undoIds.includes(item.id)))
  const undo = async () => {
    if (!canUndo || !documentId || undoBusy || verifying) return
    const request = ++version.current
    setUndoBusy(true)
    try {
      if (undoIds.length === 1) await scansApi.delete(undoIds[0])
      else await scansApi.deleteBulk(documentId, undoIds)
      const store = useScanStore.getState()
      if (store.document?.id === documentId) store.setScans(store.scans.filter(item => !undoIds.includes(item.id)))
      if (request === version.current) {
        setUndoIds([])
        setOutcome({ documentId, message: 'Последний скан отменён' })
      }
    } catch {
      if (request === version.current) setOutcome({ documentId, message: 'Не удалось отменить скан. Повторите попытку.' })
    } finally {
      setUndoBusy(false)
    }
  }

  return (
    <div className="scan-input">
      <div className="field-label">Сканирование</div>
      <div className="scan-input__row">
        <div
          className="scan-input__field scan-input__com-status"
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            gap: 6,
            ...(deleteMode ? { border: '2px solid #dc2626', background: 'var(--st-err-bg)', padding: 6, borderRadius: 6 } : {}),
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {serial.connected ? (
              <span className="badge badge--ok"><span className="badge__dot" /> COM-порт подключён</span>
            ) : (
              <span className="badge badge--warn">
                <span className="badge__dot" /> COM-порт не подключён
              </span>
            )}
            {/* Выбор/выдача порта делается ЗДЕСЬ — в отдельной top-level вкладке
                сканирования, где Web Serial разрешён. В окне МС (iframe) доступ к
                serial заблокирован Permissions-Policy, поэтому кнопки нет в Настройках. */}
            {serial.connected ? (
              <button
                type="button"
                className="button button--sm"
                onClick={() => void serial.disconnect()}
              >
                Отключить
              </button>
            ) : (
              <button
                type="button"
                className="button button--sm button--success"
                onClick={() => void serial.requestConnect()}
                disabled={!serial.supported}
              >
                <Icon name="box" size={14} /> Подключить COM-порт
              </button>
            )}
            {documentId && <button
              type="button"
              className="button button--sm"
              onClick={() => setManualOpen((open) => !open)}
              disabled={!documentId}
            >
              {manualOpen ? 'Скрыть ручной ввод' : 'Ввести вручную'}
            </button>}
          </div>
          {!serial.supported && (
            <div className="alert alert--warn" style={{ width: '100%', margin: 0 }}>
              Браузер не поддерживает Web Serial API. Откройте окно сканирования в
              Chrome или Edge по HTTPS.
            </div>
          )}
          {serial.supported && serial.error && (
            <div className="alert alert--error" style={{ width: '100%', margin: 0 }}>
              {serial.error}
            </div>
          )}
        </div>
        {documentId && <details>
          <summary>Параметры сканирования</summary>
          <div className="scan-input__tools">
            {!deleteMode && <button type="button" className="button"
              onClick={() => setDeleteMode(true)}>
              <Icon name="trash" size={15} /> Удалить сканированием
            </button>}
            <button type="button" className="button" onClick={() => setUnpackBox(!unpackBox)}
              title="Раскрывать короб на штучные марки или сохранять целиком">
              <Icon name="box" size={15} /> {unpackBox ? 'Короб: раскрывать' : 'Короб: целиком'}
            </button>
            <button type="button" className="button" onClick={() => setSearchOpen(true)}>
              <Icon name="filter" size={15} /> Поиск марки
            </button>
          </div>
        </details>}
      </div>
      {documentId && manualOpen && (
        <form
          className="field-row mt-8"
          onSubmit={(event) => void handleManualSubmit(event)}
          style={{ alignItems: 'stretch' }}
        >
          <input
            value={manualValue}
            onChange={(event) => setManualValue(event.target.value)}
            placeholder={deleteMode ? 'Введите код для удаления…' : 'Введите код вручную…'}
            disabled={!documentId}
            className="scan-input__field"
            autoComplete="off"
            spellCheck={false}
            autoFocus
          />
          <button type="submit" className={deleteMode ? "button button--danger" : "button button--success"} disabled={!documentId || !manualValue.trim()}>
            {deleteMode ? 'Удалить' : 'Добавить'}
          </button>
        </form>
      )}
      {documentId && deleteMode && (
        <div className="alert alert--error" style={{ marginTop: 8 }}>
          Режим удаления: следующий отсканированный код будет удалён из списка.
          <button type="button" className="button button--sm" onClick={() => setDeleteMode(false)}>Выйти из удаления</button>
        </div>
      )}
      <div aria-live="polite" aria-atomic="true">
        {scan && <div className="scan-result">
          <strong>{row?.product_name || scan.product_name || 'Марка'}</strong>
          <span>{resultText}{row && row.expected > 0 && ` · ${row.addedTotal} из ${row.expected}`}</span>
        </div>}
        {current?.message && <p>{current.message}</p>}
      </div>
      {canUndo && <button type="button" className="button button--sm" disabled={undoBusy || verifying}
        onClick={() => void undo()}>Отменить последний скан</button>}
      {!documentId && inactiveHint && (
        <p className="hint">{inactiveHint}</p>
      )}
      <CodeSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} serial={serial} />
    </div>
  )
}
