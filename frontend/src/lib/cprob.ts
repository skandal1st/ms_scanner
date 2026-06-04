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
function waitForCadesGlobal(timeoutMs = 10000): Promise<AnyPlugin> {
  if (window.cadesplugin) return Promise.resolve(window.cadesplugin)
  return new Promise((resolve, reject) => {
    let done = false
    const finish = (ok: boolean) => {
      if (done) return
      done = true
      window.removeEventListener('cadesplugin_loaded', onEvent)
      window.clearInterval(poll)
      window.clearTimeout(timer)
      if (ok && window.cadesplugin) resolve(window.cadesplugin)
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

/** Дождаться, когда cadesplugin будет готов (плагин инициализируется асинхронно). */
async function waitForPlugin(): Promise<AnyPlugin> {
  const cp = await waitForCadesGlobal()
  // Современные сборки плагина возвращают thenable — ждём готовности.
  if (typeof cp.then === 'function') {
    await new Promise<void>((resolve, reject) => {
      try {
        cp.then(() => resolve(), (err: unknown) => reject(err))
      } catch (e) {
        reject(e)
      }
    })
  }
  return cp
}

export async function isPluginAvailable(): Promise<boolean> {
  try {
    await waitForPlugin()
    return true
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
  const cp = await waitForPlugin()
  const store = await cp.CreateObjectAsync('CAPICOM.Store')
  await store.Open(
    cp.CAPICOM_CURRENT_USER_STORE,
    cp.CAPICOM_MY_STORE,
    cp.CAPICOM_STORE_OPEN_READ_ONLY,
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
}

/** Подписать строку detached CAdES-BES сертификатом по отпечатку. */
export async function signDataCadesBes(
  thumbprint: string,
  data: string,
): Promise<string> {
  const cp = await waitForPlugin()
  const store = await cp.CreateObjectAsync('CAPICOM.Store')
  await store.Open(
    cp.CAPICOM_CURRENT_USER_STORE,
    cp.CAPICOM_MY_STORE,
    cp.CAPICOM_STORE_OPEN_READ_ONLY,
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
    await signedData.propset_Content(data)

    // detached=true → подпись без оборачивания исходных данных.
    const signature: string = await signedData.SignCades(
      signer,
      cp.CADESCOM_CADES_BES,
      true,
    )
    return signature
  } finally {
    try {
      await store.Close()
    } catch {
      /* ignore */
    }
  }
}
