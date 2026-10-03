import { useEffect, useRef } from 'react'
import { decodeJwtSub } from '../lib/jwt'

export interface DocumentEvent {
  type: string
  document_id?: string
  [key: string]: any
}

// Incremental delivery during scanning, snapshots on reconnect and as a recovery fallback.
export function useDocumentLive(documentId: string | null, terminal: boolean,
  onEvent: (event: DocumentEvent) => void, reconcile: () => void) {
  const callbacks = useRef({ onEvent, reconcile })
  callbacks.current = { onEvent, reconcile }
  useEffect(() => {
    if (!documentId) return
    let stopped = false
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let delay = 500
    const connect = () => {
      const token = localStorage.getItem(terminal ? 'tsd_access_token' : 'access_token')
      const userId = token && decodeJwtSub(token)
      if (!token || (!terminal && !userId) || stopped) return
      const path = terminal ? `tsd/${documentId}` : userId
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/${path}?token=${encodeURIComponent(token)}`)
      socket.onopen = () => { delay = 500; callbacks.current.reconcile() }
      socket.onmessage = message => {
        try {
          const event = JSON.parse(message.data) as DocumentEvent
          if (event.document_id && event.document_id !== documentId) return
          callbacks.current.onEvent(event)
        } catch { /* Ignore malformed messages; the recovery snapshot stays available. */ }
      }
      socket.onclose = () => {
        if (stopped) return
        callbacks.current.reconcile()
        retry = setTimeout(connect, delay)
        delay = Math.min(delay * 2, 10_000)
      }
    }
    connect()
    const heartbeat = setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send('ping')
    }, 15_000)
    const resume = () => {
      if (document.visibilityState === 'visible') callbacks.current.reconcile()
    }
    const recovery = setInterval(resume, 15_000)
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('online', resume)
    return () => {
      stopped = true
      clearTimeout(retry)
      clearInterval(heartbeat)
      clearInterval(recovery)
      document.removeEventListener('visibilitychange', resume)
      window.removeEventListener('online', resume)
      socket?.close()
    }
  }, [documentId, terminal])
}
