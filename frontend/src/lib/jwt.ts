/**
 * Читает `sub` из JWT без проверки подписи (достаточно для WebSocket URL и user_id в localStorage).
 */
export function decodeJwtSub(token: string): string | null {
  try {
    const parts = token.split('.')
    if (parts.length < 2) return null
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = b64.padEnd(Math.ceil(b64.length / 4) * 4, '=')
    const payload = JSON.parse(atob(padded)) as { sub?: string }
    return typeof payload.sub === 'string' ? payload.sub : null
  } catch {
    return null
  }
}

export function persistUserIdFromAccessToken(token: string): void {
  const sub = decodeJwtSub(token)
  if (sub) localStorage.setItem('user_id', sub)
}
