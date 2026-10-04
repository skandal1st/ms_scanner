export const TSD_APK_PATH = '/downloads/skandata-tsd.apk'
export function tsdDocumentLink(id: string): string { return `skandata:${id}` }
export function parseTsdDocumentCode(raw: string): string | null {
  const value = raw.trim()
  if (value.startsWith('SKANDATA:DOCUMENT:')) return value.slice('SKANDATA:DOCUMENT:'.length)
  if (value.startsWith('skandata:')) {
    const id = value.slice('skandata:'.length)
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ? id : null
  }
  try { return new URL(value).searchParams.get('document') } catch { return null }
}
