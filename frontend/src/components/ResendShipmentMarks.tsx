import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { shipmentCorrectionsApi } from '../api/client'
import { useModal } from './ModalProvider'

export function ResendShipmentMarks({ documentId, terminal = false, disabled = false, loadState = true }: {
  documentId: string; terminal?: boolean; disabled?: boolean; loadState?: boolean
}) {
  const modal = useModal()
  const qc = useQueryClient()
  const api = useMemo(() => shipmentCorrectionsApi(terminal), [terminal])
  const [started, setStarted] = useState(false)
  const [requesting, setRequesting] = useState(false)
  const [error, setError] = useState('')
  const requestRef = useRef(false)
  const completed = useRef<string | null>(null)
  const key = ['shipment-mark-resend', terminal, documentId]
  const status = useQuery({ queryKey: key, enabled: loadState || started,
    queryFn: () => api.resendStatus(documentId).then(r => r.data),
    refetchInterval: query => ['queued', 'running'].includes(query.state.data?.status || '') ? 1500 : false })
  const state = status.data
  const active = state?.status === 'queued' || state?.status === 'running'
  const stale = active && Boolean(state?.updated_at) && Date.now() - Date.parse(state!.updated_at!) > 15 * 60_000
  useEffect(() => {
    if (state?.status === 'done' && state.id !== completed.current) {
      completed.current = state.id || null
      void qc.invalidateQueries({ queryKey: ['shipment-correction'] })
    }
  }, [state?.status, state?.id, qc])
  const resend = async () => {
    if (requestRef.current || (active && !stale) || disabled) return
    requestRef.current = true
    setRequesting(true)
    try {
      const ok = await modal.confirm('Сверить марки сохранённой сборки с МойСкладом и отправить только недостающие? Товары, количества и цены останутся прежними. Несохранённые изменения исправления нужно сначала сохранить или отменить.',
        { title: 'Повторная отправка марок', okText: 'Сверить и отправить' })
      if (!ok) return
      setError('')
      const { data } = await api.resendMarks(documentId)
      qc.setQueryData(key, data)
      setStarted(true)
    } catch (e) {
      setError((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Не удалось запустить сверку. Проверьте связь и повторите попытку.')
    } finally {
      requestRef.current = false
      setRequesting(false)
    }
  }
  return <div className="shipment-mark-resend">
    <button type="button" className={terminal ? 'tsd-button' : 'button button--sm'}
      disabled={disabled || requesting || (active && !stale)} aria-busy={requesting || (active && !stale)} onClick={() => void resend()}>
      {stale ? 'Повторить сверку марок' : active ? 'Сверяем и отправляем марки…' : state?.status === 'error' ? 'Повторить сверку марок' : 'Повторно отправить марки'}
    </button>
    {active && <span role="status" className="hint">{state.expected
      ? `Кодов сборки: ${state.expected}. Уже в МС: ${state.already || 0}. Добавлено: ${state.added || 0}. Проверяем сохранение…`
      : 'Задание поставлено в очередь. Проверяем состав отгрузки…'}</span>}
    {stale && <span role="alert" className="hint">Состояние давно не обновлялось. Можно повторить сверку: уже работающая отправка не запустится второй раз.</span>}
    {state?.status === 'done' && <span role="status" className="shipment-mark-resend__success">
      В МС подтверждено кодов сборки: {state.confirmed}. Добавлено: {state.added || 0}, уже было: {state.already || 0}.
    </span>}
    {(error || state?.error || status.error) && <span role="alert" className="error">{error || state?.error || 'Не удалось обновить состояние сверки. Проверьте связь.'}</span>}
  </div>
}
