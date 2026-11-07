// Read on each action: a device's timezone can change while the page is open.
export function getClientTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null
  } catch {
    return null
  }
}

export function getClientTimezoneContext(): { timezone: string | null; utc_offset_minutes: number | null } {
  let offset: number | null = null
  try {
    // Date is also available on older Android WebViews without Intl timezone data.
    const detected = -new Date().getTimezoneOffset()
    if (Number.isInteger(detected) && detected >= -720 && detected <= 840) offset = detected
  } catch { /* The server will request an explicit organization timezone. */ }
  return { timezone: getClientTimezone(), utc_offset_minutes: offset }
}

export const RUSSIAN_TIMEZONES = [
  ['Europe/Kaliningrad', 'Калининград'],
  ['Europe/Moscow', 'Москва'],
  ['Europe/Samara', 'Самара'],
  ['Asia/Yekaterinburg', 'Екатеринбург'],
  ['Asia/Omsk', 'Омск'],
  ['Asia/Krasnoyarsk', 'Красноярск'],
  ['Asia/Irkutsk', 'Иркутск'],
  ['Asia/Yakutsk', 'Якутск'],
  ['Asia/Vladivostok', 'Владивосток'],
  ['Asia/Magadan', 'Магадан'],
  ['Asia/Kamchatka', 'Камчатка'],
] as const

export function timezoneOptions(extra: (string | null | undefined)[] = []): string[] {
  // Older TSD browsers may not implement supportedValuesOf yet.
  const supported = Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] }
  let zones: string[] = []
  try { zones = supported.supportedValuesOf?.('timeZone') ?? [] } catch { /* use the fallback list */ }
  return [...new Set([...RUSSIAN_TIMEZONES.map(([zone]) => zone), ...zones, ...extra.filter((v): v is string => !!v)])]
}

export function timezoneLabel(zone: string): string {
  const city = RUSSIAN_TIMEZONES.find(([value]) => value === zone)?.[1]
  return city ? `${city} (${zone})` : zone
}
