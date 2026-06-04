// Обёртка над CryptoPro Browser Plugin (window.cadesplugin).
// Используется для входа в Честный Знак по УКЭП: challenge от ЧЗ подписывается
// сертификатом из CSP клиента, бэк меняет подпись на access_token.
//
// Сам файл cadesplugin_api.js берётся из дистрибутива КриптоПро Browser Plugin
// и кладётся в frontend/public/cadesplugin_api.js (подключается из index.html).

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

/** Дождаться, когда cadesplugin будет готов (плагин инициализируется асинхронно). */
async function waitForPlugin(): Promise<AnyPlugin> {
  const cp = window.cadesplugin
  if (!cp) {
    throw new Error(
      'КриптоПро Browser Plugin не обнаружен. Установите расширение и перезагрузите страницу.',
    )
  }
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
  } catch {
    return false
  }
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
