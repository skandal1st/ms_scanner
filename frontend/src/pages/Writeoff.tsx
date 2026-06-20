import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ScanInput } from '../components/ScanInput'
import { CodesTable } from '../components/CodesTable'
import { StatsPanel } from '../components/StatsPanel'
import { DocumentSelector } from '../components/DocumentSelector'
import { ManualProductTargetBar } from '../components/ManualProductTargetBar'
import { useScanStore } from '../store/scanStore'
import { useLoadDocument } from '../hooks/useDocuments'
import { listCertificates, signDetachedBase64, type CzCertificate } from '../lib/cprob'
import { czApi, integrationsApi, WRITEOFF_REASONS, type Document } from '../api/client'

type Phase = 'idle' | 'signing' | 'submitting' | 'processing' | 'done' | 'error'

export function WriteoffPage() {
  const {
    document,
    setDocument,
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
        </div>
        <span className="acc-header__doc">{document?.name ?? 'Документ не выбран'}</span>
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
            Войдите в Честный Знак — без авторизации списание невозможно.
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
        <div className="acc-left">
          <DocumentSelector kind="loss" onSelect={handleSelectDoc} selected={document} />
          <ManualProductTargetBar />
          <ScanInput documentId={document?.id ?? null} />
          <StatsPanel />

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
          <div className="acc-table-wrap">
            <CodesTable />
          </div>
        </div>
      </div>

      <footer className="acc-footer">
        <div className="acc-footer__spacer" />
        {message && phase === 'error' && (
          <span style={{ color: '#b91c1c', fontSize: 12, marginRight: 12 }}>{message}</span>
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
