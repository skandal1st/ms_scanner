import { useState, useCallback, type FormEvent } from 'react'
import { useScanner } from '../hooks/useScanner'
import { useSerialScanner } from '../hooks/useSerialScanner'
import { normalizeScannerInput } from '../lib/scannerLayout'
import { useScanStore } from '../store/scanStore'
import { CodeSearchModal } from './CodeSearchModal'
import { Icon } from './Icon'

interface Props {
  documentId: string | null
}

export function ScanInput({ documentId }: Props) {
  const [manualValue, setManualValue] = useState('')
  const [manualOpen, setManualOpen] = useState(false)
  const [lastCode, setLastCode] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const { submitCode } = useScanner(documentId)
  const deleteMode = useScanStore((s) => s.deleteMode)
  const setDeleteMode = useScanStore((s) => s.setDeleteMode)
  const unpackBox = useScanStore((s) => s.unpackBox)
  const setUnpackBox = useScanStore((s) => s.setUnpackBox)

  const handleScannedCode = useCallback(
    async (code: string) => {
      const trimmed = code.trim()
      if (!trimmed) return
      setLastCode(trimmed)
      await submitCode(trimmed)
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
            <button
              type="button"
              className="button button--sm"
              onClick={() => setManualOpen((open) => !open)}
              disabled={!documentId}
            >
              {manualOpen ? 'Скрыть ручной ввод' : 'Ввести вручную'}
            </button>
          </div>
          {!serial.supported && (
            <div className="alert alert--warn" style={{ width: '100%', margin: 0 }}>
              Браузер не поддерживает Web Serial API. Откройте окно сканирования в
              Chrome или Edge по HTTPS.
            </div>
          )}
          {serial.error && (
            <div className="alert alert--error" style={{ width: '100%', margin: 0 }}>
              {serial.error}
            </div>
          )}
        </div>
        <button
          type="button"
          className="button scan-input__camera"
          disabled={!documentId}
          onClick={() => setDeleteMode(!deleteMode)}
          style={
            deleteMode
              ? { background: 'var(--st-err-fg)', borderColor: 'var(--st-err-fg)', color: '#fff' }
              : undefined
          }
          title={
            deleteMode
              ? 'Выключить режим удаления'
              : 'Включить режим: следующий отсканированный код будет удалён из списка'
          }
        >
          {deleteMode ? (
            <><Icon name="close" size={15} /> Удаление</>
          ) : (
            <><Icon name="trash" size={15} /> Удалить</>
          )}
        </button>
        <button
          type="button"
          className="button scan-input__camera"
          disabled={!documentId}
          onClick={() => setUnpackBox(!unpackBox)}
          title={
            unpackBox
              ? 'Короб раскрывается на штучные КМ. Нажмите, чтобы сохранять короб целиком (transportpack).'
              : 'Короб сохраняется целиком (transportpack). Нажмите, чтобы раскрывать на штучные КМ.'
          }
        >
          <Icon name="box" size={15} /> {unpackBox ? 'Короб: раскрывать' : 'Короб: целиком'}
        </button>
        <button
          type="button"
          className="button scan-input__camera"
          onClick={() => setSearchOpen(true)}
          title="Найти, в каком документе уже есть марка"
        >
          <Icon name="filter" size={15} /> Поиск марки
        </button>
      </div>
      {manualOpen && (
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
          <button type="submit" className="button button--success" disabled={!documentId || !manualValue.trim()}>
            Добавить
          </button>
        </form>
      )}
      {deleteMode && (
        <div className="alert alert--error" style={{ marginTop: 8 }}>
          Режим удаления: следующий отсканированный код будет удалён из списка.
        </div>
      )}
      {lastCode && (
        <div className="scan-input__last">
          <span>Последний:</span>
          <code>{lastCode.slice(0, 30)}{lastCode.length > 30 ? '…' : ''}</code>
        </div>
      )}
      {!documentId && (
        <p className="hint">Выберите документ для начала сканирования</p>
      )}
      <CodeSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  )
}
