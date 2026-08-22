import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ScanInput } from '../components/ScanInput'
import { CodesTable } from '../components/CodesTable'
import { StatsPanel } from '../components/StatsPanel'
import { DocumentSelector } from '../components/DocumentSelector'
import { ProgressTable } from '../components/ProgressTable'
import { ManualProductTargetBar } from '../components/ManualProductTargetBar'
import { Icon } from '../components/Icon'
import { useScanStore } from '../store/scanStore'
import { useLoadDocument } from '../hooks/useDocuments'
import { listCertificates, signDetachedBase64, type CzCertificate } from '../lib/cprob'
import {
  czApi,
  integrationsApi,
  WRITEOFF_REASONS,
  type Document,
  type UnresolvedCode,
} from '../api/client'

type Phase = 'idle' | 'signing' | 'submitting' | 'processing' | 'done' | 'error'

export function WriteoffPage() {
  const {
    document,
    setDocument,
    reset,
    stats,
    scans,
    czTokenExpired,
    setCzTokenExpired,
    writeoffResult,
    setWriteoffResult,
  } = useScanStore()
  const qc = useQueryClient()

  const [pendingDoc, setPendingDoc] = useState<Document | null>(null)
  const [reason, setReason] = useState(WRITEOFF_REASONS[0].value)
  const [basisNumber, setBasisNumber] = useState('')
  const [basisDate, setBasisDate] = useState('')
  const [certs, setCerts] = useState<CzCertificate[]>([])
  const [thumbprint, setThumbprint] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState<string | null>(null)
  // Марки, которые ЧЗ не смог найти — списать их нельзя, показываем списком.
  const [unresolved, setUnresolved] = useState<UnresolvedCode[]>([])

  useLoadDocument(pendingDoc?.id ?? null)

  // Сбрасываем возможный устаревший результат прошлой сессии при входе на страницу.
  useEffect(() => {
    setWriteoffResult(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { data: integration } = useQuery({
    queryKey: ['integration'],
    queryFn: () => integrationsApi.get().then((r) => r.data),
  })

  // Сертификаты для подписи документа вывода из оборота (как при входе в ЧЗ).
  useEffect(() => {
    let cancelled = false
    void listCertificates()
      .then((list) => {
        if (cancelled) return
        setCerts(list)
        if (list.length) {
          // По возможности — сертификат, которым входили в ЧЗ.
          const match = list.find((c) => c.subject === integration?.cz_cert_subject)
          setThumbprint((match ?? list[0]).thumbprint)
        }
      })
      .catch(() => {
        /* плагин/сертификаты недоступны — отдельная ошибка покажется при попытке списания */
      })
    return () => {
      cancelled = true
    }
  }, [integration?.cz_cert_subject])

  // Результат списания по WS.
  useEffect(() => {
    if (!writeoffResult) return
    if (writeoffResult.status === 'done') {
      setPhase('done')
      setMessage(null)
      if (document) qc.invalidateQueries({ queryKey: ['document', document.id] })
    } else {
      setPhase('error')
      setMessage(writeoffResult.error || 'Честный Знак не обработал документ')
    }
  }, [writeoffResult])

  const handleSelectDoc = (doc: Document) => {
    setPendingDoc(doc)
    setDocument(doc)
    setPhase('idle')
    setMessage(null)
    setWriteoffResult(null)
    setUnresolved([])
  }

  // Отвязаться от текущего документа → вернуться к выбору (без F5). Сканы остаются в БД.
  const handleDetach = () => {
    reset()
    setPendingDoc(null)
    setPhase('idle')
    setMessage(null)
    setWriteoffResult(null)
    setUnresolved([])
  }

  const canSubmit =
    !!document &&
    scans.length > 0 &&
    stats.valid + stats.overflow > 0 &&
    phase !== 'signing' &&
    phase !== 'submitting' &&
    phase !== 'processing'

  const writeoffCount = stats.valid + stats.overflow

  const handleWriteoff = async () => {
    if (!document) return
    setMessage(null)
    setWriteoffResult(null)
    setUnresolved([])

    if (!integration?.has_cz) {
      setPhase('error')
      setMessage('Войдите в Честный Знак по УКЭП (раздел «Настройки»)')
      return
    }
    if (!integration?.cz_inn) {
      setPhase('error')
      setMessage('Не задан ИНН участника оборота — укажите его в «Настройках»')
      return
    }
    if (!thumbprint) {
      setPhase('error')
      setMessage('Не выбран сертификат для подписи')
      return
    }

    try {
      setPhase('signing')
      const { data: prep } = await czApi.writeoffPrepare({
        document_id: document.id,
        reason,
        basis_number: basisNumber.trim() || undefined,
        basis_date: basisDate || undefined,
      })

      setUnresolved(prep.unresolved ?? [])

      // Ни одной марки нельзя списать (все не найдены в ЧЗ) — не подписываем.
      if (!prep.writeoff_token || prep.parts.length === 0) {
        setPhase('error')
        setMessage(
          'Ни одну марку не удалось подготовить к списанию — см. список ниже.',
        )
        return
      }

      const signatures: { pg: string; signature: string }[] = []
      for (const part of prep.parts) {
        const signature = await signDetachedBase64(thumbprint, part.product_document_b64)
        signatures.push({ pg: part.pg, signature })
      }

      setPhase('submitting')
      await czApi.writeoffSubmit({ writeoff_token: prep.writeoff_token, signatures })

      // Дальше воркер опрашивает статус — ждём WS-событие writeoff_status.
      setPhase('processing')
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      const detail = ax?.response?.data?.detail
      setPhase('error')
      setMessage(detail || (e instanceof Error ? e.message : String(e)))
    }
  }

  const reasonLabel = useMemo(
    () => WRITEOFF_REASONS.find((r) => r.value === reason)?.label ?? reason,
    [reason],
  )

  const phaseText: Record<Phase, string> = {
    idle: `Списать ${writeoffCount}`,
    signing: 'Подпишите документ…',
    submitting: 'Отправка в ЧЗ…',
    processing: 'Обрабатывается в ЧЗ…',
    done: 'Списано ✓',
    error: `Списать ${writeoffCount}`,
  }

  return (
    <div className="acc-page">
      <header className="acc-header">
        <div className="flex-row gap-8" style={{ alignItems: 'center' }}>
          <h1 className="acc-header__title">Списание маркировки</h1>
          {phase === 'done' && <span className="badge badge--ok">Завершено</span>}
          {phase === 'processing' && <span className="badge badge--info">Обрабатывается</span>}
          {document && (
            <button
              type="button"
              className="button button--sm"
              onClick={handleDetach}
              title="Отвязаться и выбрать другой документ (сканы остаются)"
            >
              <Icon name="close" size={14} /> Отвязаться
            </button>
          )}
        </div>
        <span className="acc-header__doc">{document?.name ?? 'Документ не выбран'}</span>
      </header>

      {czTokenExpired && (
        <div role="alert" className="alert alert--error" style={{ margin: '12px 18px 0' }}>
          <span className="alert__spacer">
            Войдите в Честный Знак — без авторизации списание невозможно.
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

      <div className="acc-body">
        <div className="acc-left">
          <DocumentSelector
            kind="loss"
            msKind="demand"
            onSelect={handleSelectDoc}
            selected={document}
          />
          <ManualProductTargetBar />
          <ScanInput documentId={document?.id ?? null} />
          <StatsPanel />
          <ProgressTable />

          <div className="section" style={{ marginTop: 12 }}>
            <label className="field-label">Причина списания</label>
            <select
              className="ui-select ui-input--block"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            >
              {WRITEOFF_REASONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>

            <label className="field-label" style={{ marginTop: 8 }}>
              Документ-основание (необязательно)
            </label>
            <div className="flex-row gap-8">
              <input
                value={basisNumber}
                onChange={(e) => setBasisNumber(e.target.value)}
                placeholder="№ акта"
                className="ui-input"
                style={{ flex: 1 }}
              />
              <input
                type="date"
                value={basisDate}
                onChange={(e) => setBasisDate(e.target.value)}
                className="ui-input"
                style={{ flex: 1 }}
              />
            </div>

            {certs.length > 1 && (
              <>
                <label className="field-label" style={{ marginTop: 8 }}>
                  Сертификат подписи
                </label>
                <select
                  className="ui-select ui-input--block"
                  value={thumbprint}
                  onChange={(e) => setThumbprint(e.target.value)}
                >
                  {certs.map((c) => (
                    <option key={c.thumbprint} value={c.thumbprint}>
                      {c.subject}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>
        </div>

        <div className="acc-right">
          <div className="acc-right__head">
            <span className="h3" style={{ margin: 0 }}>Коды маркировки</span>
            <span className="text-muted" style={{ fontSize: 11 }}>{scans.length} шт.</span>
          </div>
          {unresolved.length > 0 && (
            <div
              role="alert"
              style={{
                margin: '0 0 10px',
                padding: 12,
                background: 'var(--st-err-bg)',
                border: '1px solid var(--st-err-bd)',
                borderRadius: 'var(--r-md)',
              }}
            >
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--st-err-fg)', marginBottom: 6 }}>
                Не удалось списать ({unresolved.length}) — Честный Знак не нашёл эти марки
              </div>
              <div style={{ maxHeight: 160, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
                {unresolved.map((u) => (
                  <div key={u.cis} style={{ fontSize: 11, color: 'var(--st-err-fg)', display: 'flex', gap: 8 }}>
                    <code style={{ flex: 1, wordBreak: 'break-all' }}>
                      {u.cis.slice(0, 40)}{u.cis.length > 40 ? '…' : ''}
                    </code>
                    <span style={{ flexShrink: 0, color: 'var(--st-err-fg)', fontWeight: 600 }}>{u.reason}</span>
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 11, color: 'var(--st-err-fg)', marginTop: 6, opacity: 0.85 }}>
                Остальные марки списываются как обычно. Проверьте эти коды и удалите
                из документа, если они не подлежат списанию.
              </div>
            </div>
          )}
          <div className="acc-table-wrap">
            <CodesTable />
          </div>
        </div>
      </div>

      <footer className="acc-footer">
        <div className="acc-footer__spacer" />
        {message && phase === 'error' && (
          <span style={{ color: 'var(--st-err-fg)', fontSize: 12, marginRight: 12 }}>{message}</span>
        )}
        <button
          type="button"
          className="button button--success"
          disabled={!canSubmit}
          title={reasonLabel}
          onClick={handleWriteoff}
        >
          {phaseText[phase]}
        </button>
      </footer>
    </div>
  )
}
