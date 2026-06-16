// Обёртка над КриптоПро ЭЦП Browser Plug-in (window.cadesplugin).
// Используется для входа в Честный Знак по УКЭП: challenge от ЧЗ подписывается
// сертификатом из CSP клиента, бэк меняет подпись на access_token.
//
// В современных Chrome/Edge window.cadesplugin инжектируется расширением
// «CAdES Browser Plugin» автоматически. Отдельный <script src="..."> не нужен.

// У cadesplugin нет официальных .d.ts; типизируем как any.
// Плагин сам по себе — thenable объект (с методом then), который дополнительно
// несёт константы CAPICOM_* и фабрику CreateObjectAsync.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyPlugin = any

declare global {
  interface Window {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    cadesplugin?: any
  }
}

export interface CzCertificate {
  thumbprint: string
  subject: string
  notAfter: string | null
}

/** Расширение «CAdES Browser Plugin» инжектирует window.cadesplugin через content
 *  script на document_idle и шлёт событие `cadesplugin_loaded`. Может занять до пары секунд. */
function waitForCadesGlobal(timeoutMs = 10000): Promise<void> {
  if (window.cadesplugin) return Promise.resolve()
  return new Promise<void>((resolve, reject) => {
    let done = false
    const finish = (ok: boolean) => {
      if (done) return
      done = true
      window.removeEventListener('cadesplugin_loaded', onEvent)
      window.clearInterval(poll)
      window.clearTimeout(timer)
      if (ok && window.cadesplugin) resolve()
      else
        reject(
          new Error(
            'КриптоПро ЭЦП Browser Plug-in не обнаружен. Установите расширение «CAdES Browser Plugin» для Chrome/Edge, разрешите ему работу на этом сайте и обновите страницу.',
          ),
        )
    }
    const onEvent = () => finish(true)
    window.addEventListener('cadesplugin_loaded', onEvent)
    // Fallback-поллинг: некоторые версии расширения не шлют событие.
    const poll = window.setInterval(() => {
      if (window.cadesplugin) finish(true)
    }, 300)
    const timer = window.setTimeout(() => finish(false), timeoutMs)
  })
}

/**
 * Дождаться готовности плагина. Возвращает void — НЕ объект плагина: window.cadesplugin
 * это augmented Promise (thenable), а возврат thenable из async-функции адаптируется
 * (вызывающий получил бы resolve-value = undefined вместо объекта). Поэтому ждём здесь,
 * а сам объект каждый вызывающий берёт из window.cadesplugin напрямую через getPlugin().
 */
async function ensurePluginReady(): Promise<void> {
  await waitForCadesGlobal()
  const cp = window.cadesplugin
  if (cp && typeof cp.then === 'function') {
    // Дожидаемся инициализации нативной части. Если она недоступна — промис
    // отклонится с понятной ошибкой из cadesplugin_api.js, и она пробросится выше.
    await cp
  }
}

/** Объект плагина из global (после ensurePluginReady). Без адаптации thenable. */
function getPlugin(): AnyPlugin {
  return window.cadesplugin
}

/**
 * Привести исключение плагина к читаемой строке. КриптоПро бросает не Error, а
 * объекты/строки (иначе в UI получается «[object Object]»). getLastError() —
 * официальный способ извлечь текст ошибки CAdESCOM.
 */
function formatCadesError(e: unknown): string {
  const cp = window.cadesplugin
  try {
    if (cp && typeof cp.getLastError === 'function') {
      const m = cp.getLastError(e)
      if (m) return String(m)
    }
  } catch {
    /* ignore */
  }
  if (e instanceof Error) return e.message
  if (typeof e === 'string') return e
  if (e && typeof e === 'object') {
    const anyE = e as { message?: unknown }
    if (anyE.message) return String(anyE.message)
    try {
      return JSON.stringify(e)
    } catch {
      /* ignore */
    }
  }
  return String(e)
}

export async function isPluginAvailable(): Promise<boolean> {
  try {
    await ensurePluginReady()
    const cp = getPlugin()
    return !!(cp && typeof cp.CreateObjectAsync === 'function')
  } catch (e) {
    console.warn('[cprob] plugin not available:', e)
    return false
  }
}

/** Диагностика для пользователя — что именно видит JS из плагина. */
export function diagnosePlugin(): string {
  const cp = window.cadesplugin
  const lines: string[] = []
  lines.push(`window.cadesplugin: ${typeof cp}`)
  if (cp) {
    lines.push(`typeof cp.then: ${typeof cp.then}`)
    lines.push(
      `typeof cp.CreateObjectAsync: ${typeof cp.CreateObjectAsync}`,
    )
    try {
      const keys = Object.keys(cp).slice(0, 20)
      lines.push(`keys (first 20): ${keys.join(', ')}`)
    } catch (e) {
      lines.push(`keys read error: ${e instanceof Error ? e.message : String(e)}`)
    }
    lines.push(
      `CAPICOM_CURRENT_USER_STORE: ${cp.CAPICOM_CURRENT_USER_STORE ?? 'нет'}`,
    )
  }
  lines.push(`userAgent: ${navigator.userAgent}`)
  lines.push(`isSecureContext: ${window.isSecureContext}`)
  return lines.join('\n')
}

export async function listCertificates(): Promise<CzCertificate[]> {
  await ensurePluginReady()
  const cp = getPlugin()
  try {
    const store = await cp.CreateObjectAsync('CAPICOM.Store')
    await store.Open(
      cp.CAPICOM_CURRENT_USER_STORE,
      cp.CAPICOM_MY_STORE,
      cp.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED,
    )
    try {
      const certs = await store.Certificates
      const count = await certs.Count
      const now = Date.now()
      const out: CzCertificate[] = []
      for (let i = 1; i <= count; i++) {
        const cert = await certs.Item(i)
        const thumbprint = String(await cert.Thumbprint)
        const subject = String(await cert.SubjectName)
        let notAfter: string | null = null
        try {
          const raw = await cert.ValidToDate
          const d = new Date(raw)
          if (!isNaN(d.getTime())) {
            notAfter = d.toISOString()
            // Не показываем просроченные сертификаты.
            if (d.getTime() < now) continue
          }
        } catch {
          /* ignore */
        }
        out.push({ thumbprint, subject, notAfter })
      }
      return out
    } finally {
      try {
        await store.Close()
      } catch {
        /* ignore */
      }
    }
  } catch (e) {
    console.warn('[cprob] listCertificates failed:', e)
    throw new Error(formatCadesError(e))
  }
}

/** Подписать строку detached CAdES-BES сертификатом по отпечатку. */
export async function signDataCadesBes(
  thumbprint: string,
  data: string,
): Promise<string> {
  await ensurePluginReady()
  const cp = getPlugin()
  try {
    const store = await cp.CreateObjectAsync('CAPICOM.Store')
    await store.Open(
      cp.CAPICOM_CURRENT_USER_STORE,
      cp.CAPICOM_MY_STORE,
      cp.CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED,
    )
    try {
      const certs = await store.Certificates
      const found = await certs.Find(cp.CAPICOM_CERTIFICATE_FIND_SHA1_HASH, thumbprint)
      const cnt = await found.Count
      if (cnt < 1) {
        throw new Error('Сертификат с указанным отпечатком не найден в хранилище')
      }
      const cert = await found.Item(1)

      const signer = await cp.CreateObjectAsync('CAdESCOM.CPSigner')
      await signer.propset_Certificate(cert)

      const signedData = await cp.CreateObjectAsync('CAdESCOM.CadesSignedData')
      // ЧЗ присылает data как строку-nonce (hex) и проверяет detached-подпись над
      // её БАЙТАМИ. КриптоПро по умолчанию кодирует строку в UTF-16LE → подпись над
      // не теми байтами («Подпись невалидна, код 2»). Передаём base64(строки) и
      // ставим ContentEncoding=BASE64_TO_BINARY: КриптоПро декодирует обратно в
      // исходные байты строки и подписывает именно их (как в примере ЦРПТ).
      await signedData.propset_ContentEncoding(cp.CADESCOM_BASE64_TO_BINARY)
      await signedData.propset_Content(btoa(data))

      // ЧЗ True API (/auth/cert/) ждёт ATTACHED-подпись (detached=false): исходные
      // данные встроены в CMS. Рабочий пример ЦРПТ: SignCades(signer, CADES_BES, false).
      const signature: string = await signedData.SignCades(
        signer,
        cp.CADESCOM_CADES_BES,
        false,
      )
      return signature
    } finally {
      try {
        await store.Close()
      } catch {
        /* ignore */
      }
    }
  } catch (e) {
    console.warn('[cprob] signDataCadesBes failed:', e)
    throw new Error(formatCadesError(e))
  }
}
