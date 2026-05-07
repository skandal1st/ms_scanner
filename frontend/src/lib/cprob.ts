/**
 * Тонкая обёртка над CryptoPro Browser Plugin (cadesplugin_api.js).
 *
 * Подпись CAdES-BES в браузере клиента. Приватный ключ всё время остаётся
 * в КриптоПро CSP на машине пользователя — мы получаем только готовый blob.
 *
 * Скрипт `cadesplugin_api.js` поставляется самим КриптоПро. Положите файл из
 * дистрибутива в `frontend/public/cadesplugin_api.js` и убедитесь, что
 * `<script src="/cadesplugin_api.js">` подключён в `index.html`.
 */

declare global {
  interface Window {
    cadesplugin?: any
  }
}

// Константы CAdESCOM (значения из официального API КриптоПро).
const CAPICOM_CURRENT_USER_STORE = 2
const CAPICOM_MY_STORE = 'My'
const CAPICOM_STORE_OPEN_READ_ONLY = 0
const CADESCOM_CADES_BES = 1
const CADESCOM_BASE64_TO_BINARY = 1

export type PluginStatus = 'ok' | 'absent'

export interface CertInfo {
  thumbprint: string
  subjectName: string
  issuerName: string
  validTo: string
}

async function ready(): Promise<any> {
  const cp = window.cadesplugin
  if (!cp) throw new Error('cadesplugin not loaded')
  // cadesplugin — Promise-like (then/catch), который резолвится после инициализации NPAPI/расширения.
  await cp
  return cp
}

export async function detectPlugin(): Promise<PluginStatus> {
  if (!window.cadesplugin) return 'absent'
  try {
    const cp = await ready()
    await cp.CreateObjectAsync('CAdESCOM.About')
    return 'ok'
  } catch {
    return 'absent'
  }
}

export async function listCertificates(): Promise<CertInfo[]> {
  const cp = await ready()
  const store = await cp.CreateObjectAsync('CAdESCOM.Store')
  await store.Open(
    CAPICOM_CURRENT_USER_STORE,
    CAPICOM_MY_STORE,
    CAPICOM_STORE_OPEN_READ_ONLY,
  )

  const out: CertInfo[] = []
  try {
    const certificates = await store.Certificates
    const count = await certificates.Count
    const now = Date.now()

    for (let i = 1; i <= count; i++) {
      const cert = await certificates.Item(i)
      const hasPrivate = await cert.HasPrivateKey()
      if (!hasPrivate) continue

      const validToStr: string = await cert.ValidToDate
      if (new Date(validToStr).getTime() < now) continue

      out.push({
        thumbprint: await cert.Thumbprint,
        subjectName: await cert.SubjectName,
        issuerName: await cert.IssuerName,
        validTo: validToStr,
      })
    }
  } finally {
    await store.Close()
  }
  return out
}

async function findCertByThumbprint(cp: any, thumbprint: string) {
  const store = await cp.CreateObjectAsync('CAdESCOM.Store')
  await store.Open(
    CAPICOM_CURRENT_USER_STORE,
    CAPICOM_MY_STORE,
    CAPICOM_STORE_OPEN_READ_ONLY,
  )
  try {
    const certificates = await store.Certificates
    // Find(0=SHA1_HASH, value) — стандартный поиск по thumbprint.
    const found = await certificates.Find(0, thumbprint)
    const count = await found.Count
    if (!count) throw new Error(`Сертификат ${thumbprint} не найден`)
    return await found.Item(1)
  } finally {
    // store.Close нужно вызывать после того, как извлекли сертификат —
    // объект cert хранит ссылку на provider, store закрывается после Sign.
    await store.Close()
  }
}

/**
 * Подписывает base64-данные (challenge от ЧЗ) в формате CAdES-BES (detached).
 * Возвращает base64 подпись, готовую для отправки в `/api/v3/auth/cert/`.
 */
export async function signCadesBes(thumbprint: string, base64Data: string): Promise<string> {
  const cp = await ready()
  const cert = await findCertByThumbprint(cp, thumbprint)

  const signer = await cp.CreateObjectAsync('CAdESCOM.CPSigner')
  await signer.propset_Certificate(cert)

  const signedData = await cp.CreateObjectAsync('CAdESCOM.CadesSignedData')
  await signedData.propset_ContentEncoding(CADESCOM_BASE64_TO_BINARY)
  await signedData.propset_Content(base64Data)

  // Третий параметр true → detached signature (ЧЗ ожидает отсоединённую подпись).
  const signature: string = await signedData.SignCades(signer, CADESCOM_CADES_BES, true)
  return signature
}
