import { useEffect, useRef, useCallback } from 'react'
import { scansApi, isSscc } from '../api/client'
import { useScanStore } from '../store/scanStore'

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

export function useScanner(documentId: string | null) {
  const { addScan, updateScan } = useScanStore()
  const wsRef = useRef<WebSocket | null>(null)

  // WebSocket — получаем обновления статусов от Celery
  useEffect(() => {
    const userId = localStorage.getItem('user_id')
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
        })
        if (data.status === 'valid') playBeep('ok')
        else if (data.status === 'invalid' || data.status === 'overflow') playBeep('error')
      }
    }

    // Keepalive ping
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping')
    }, 30000)

    return () => {
      clearInterval(ping)
      ws.close()
    }
  }, [updateScan])

  const submitCode = useCallback(
    async (code: string) => {
      if (!documentId || !code.trim()) return
      const trimmed = code.trim()

      try {
        if (isSscc(trimmed)) {
          // Короб → бэкенд распакует через ЧЗ и вернёт массив сканов
          const { data: scans } = await scansApi.createBox(documentId, trimmed)
          let anyDuplicate = false
          for (const s of scans) {
            addScan(s)
            if (s.status === 'duplicate') anyDuplicate = true
          }
          playBeep(anyDuplicate ? 'error' : 'ok')
        } else {
          const { data: scan } = await scansApi.create(documentId, trimmed)
          if (scan.status === 'duplicate') {
            playBeep('error')
          }
          addScan(scan)
        }
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
