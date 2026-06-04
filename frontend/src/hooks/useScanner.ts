import { useEffect, useRef, useCallback } from 'react'
import { scansApi } from '../api/client'
import { useScanStore } from '../store/scanStore'
import { decodeJwtSub } from '../lib/jwt'

const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)()

function playBeep(type: 'ok' | 'error') {
  const osc = audioCtx.createOscillator()
  const gain = audioCtx.createGain()
  osc.connect(gain)
  gain.connect(audioCtx.destination)
  osc.type = 'sine'
  osc.frequency.value = type === 'ok' ? 880 : 220
  gain.gain.setValueAtTime(0.3, audioCtx.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + (type === 'ok' ? 0.15 : 0.4))
  osc.start()
  osc.stop(audioCtx.currentTime + (type === 'ok' ? 0.15 : 0.4))
}

function resolveWsUserId(): string | null {
  const cached = localStorage.getItem('user_id')
  if (cached) return cached
  const token = localStorage.getItem('access_token')
  if (!token) return null
  const sub = decodeJwtSub(token)
  if (sub) {
    localStorage.setItem('user_id', sub)
    return sub
  }
  return null
}

export function useScanner(documentId: string | null) {
  const { addScan, updateScan } = useScanStore()
  const wsRef = useRef<WebSocket | null>(null)

  const hasPending = useScanStore((s) => {
    if (!documentId) return false
    return s.scans.some((x) => x.document_id === documentId && x.status === 'pending')
  })

  // WebSocket — обновления статусов от Celery
  useEffect(() => {
    const userId = resolveWsUserId()
    if (!userId) return

    const wsUrl = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/${userId}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'scan_update') {
        updateScan(data.scan_id, {
          status: data.status,
          product_name: data.product_name,
          error_message: data.error_message,
          ...(data.gtin != null && data.gtin !== ''
            ? { gtin: data.gtin as string }
            : {}),
          ...(data.moysklad_product_id != null && data.moysklad_product_id !== ''
            ? { moysklad_product_id: data.moysklad_product_id as string }
            : {}),
        })
        if (data.status === 'valid') playBeep('ok')
        else if (
          data.status === 'invalid' ||
          data.status === 'overflow' ||
          data.status === 'unknown_product'
        )
          playBeep('error')
      }
    }

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping')
    }, 30000)

    return () => {
      clearInterval(ping)
      ws.close()
    }
  }, [updateScan])

  // Если WS недоступен — опрашиваем список сканов, пока есть pending
  useEffect(() => {
    if (!documentId || !hasPending) return
    const docId = documentId

    async function poll() {
      try {
        const { data } = await scansApi.list(docId)
        useScanStore.getState().setScans(data)
      } catch {
        /* сеть / 401 обработает axios */
      }
    }

    void poll()
    const interval = window.setInterval(() => void poll(), 1500)
    return () => clearInterval(interval)
  }, [documentId, hasPending])

  const submitCode = useCallback(
    async (code: string) => {
      if (!documentId || !code.trim()) return
      const trimmed = code.trim()

      try {
        const targetPid = useScanStore.getState().targetProductId
        const { data: scan } = await scansApi.create(
          documentId,
          trimmed,
          targetPid || undefined
        )
        if (scan.status === 'duplicate') {
          playBeep('error')
        }
        addScan(scan)
      } catch (err: unknown) {
        playBeep('error')
        const ax = err as { response?: { data?: { detail?: unknown } } }
        const d = ax?.response?.data?.detail
        if (typeof d === 'string' && d) window.alert(d)
        console.error('Scan error:', err)
      }
    },
    [documentId, addScan]
  )

  return { submitCode }
}
