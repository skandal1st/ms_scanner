import { useState, type CSSProperties } from 'react'

/**
 * Диагностика доступности Web Serial (COM-сканер) в текущем контексте.
 *
 * Цель — фактически подтвердить/опровергнуть гипотезу «navigator.serial запрещён
 * Permissions-Policy внутри iframe МойСклада». Компонент временный: разворачиваем
 * на dev, открываем страницу /ms в реальном iframe МС, жмём «Запросить COM-порт»
 * и смотрим вердикт. Тот же самый компонент открываем на skandata.ru напрямую
 * (top-level) для сравнения. Разница = ограничение фрейма.
 *
 * Как читать результат кнопки requestPort():
 *  - открылся системный выбор порта → serial В IFRAME РАБОТАЕТ (гибрид не нужен);
 *  - SecurityError / «disallowed by permissions policy» / «permissions policy» →
 *    ЗАБЛОКИРОВАНО политикой фрейма (МС не выставил allow="serial") — подтверждает
 *    необходимость гибрида (COM только в отдельной вкладке);
 *  - NotFoundError / «No port selected» → API доступен, вы просто закрыли диалог.
 */

interface ProbeRow {
  label: string
  value: string
  ok: boolean | null // true=хорошо, false=плохо, null=нейтрально/инфо
}

function detectContext(): ProbeRow {
  let framed = false
  try {
    framed = window.self !== window.top
  } catch {
    // Кросс-доменный доступ к window.top кидает SecurityError — значит мы точно во фрейме.
    framed = true
  }
  return {
    label: 'Контекст',
    value: framed ? 'внутри iframe' : 'top-level (отдельная вкладка/окно)',
    ok: null,
  }
}

function detectApiPresent(): ProbeRow {
  const present = typeof navigator !== 'undefined' && 'serial' in navigator
  return {
    label: "'serial' in navigator",
    value: present ? 'да — API объявлен' : 'нет — API скрыт в этом контексте',
    ok: present,
  }
}

async function detectPermission(): Promise<ProbeRow> {
  try {
    const perms = navigator.permissions as unknown as {
      query?: (d: { name: string }) => Promise<{ state: string }>
    }
    if (!perms?.query) {
      return { label: 'permissions.query(serial)', value: 'не поддерживается браузером', ok: null }
    }
    const res = await perms.query({ name: 'serial' })
    return { label: 'permissions.query(serial)', value: res.state, ok: res.state !== 'denied' }
  } catch (e) {
    const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e)
    return { label: 'permissions.query(serial)', value: msg, ok: null }
  }
}

function detectFeaturePolicy(): ProbeRow {
  try {
    const fp = (document as unknown as {
      featurePolicy?: { allowsFeature?: (f: string) => boolean }
    }).featurePolicy
    if (!fp?.allowsFeature) {
      return { label: 'featurePolicy.allowsFeature(serial)', value: 'API недоступен (устар.)', ok: null }
    }
    const allowed = fp.allowsFeature('serial')
    return { label: 'featurePolicy.allowsFeature(serial)', value: allowed ? 'allowed' : 'blocked', ok: allowed }
  } catch (e) {
    const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e)
    return { label: 'featurePolicy.allowsFeature(serial)', value: msg, ok: null }
  }
}

export function SerialIframeDiag() {
  const [rows, setRows] = useState<ProbeRow[] | null>(null)
  const [portResult, setPortResult] = useState<{ text: string; ok: boolean | null } | null>(null)
  const [busy, setBusy] = useState(false)

  const runProbes = async () => {
    const base = [detectContext(), detectApiPresent(), detectFeaturePolicy()]
    const perm = await detectPermission()
    setRows([...base, perm])
  }

  const requestPort = async () => {
    setBusy(true)
    setPortResult(null)
    try {
      const nav = navigator as Navigator & { serial?: { requestPort: () => Promise<unknown> } }
      if (!nav.serial) {
        setPortResult({ text: 'navigator.serial отсутствует — API не объявлен в этом контексте.', ok: false })
        return
      }
      await nav.serial.requestPort()
      setPortResult({ text: '✅ Открылся выбор порта — Web Serial В ЭТОМ КОНТЕКСТЕ РАБОТАЕТ.', ok: true })
    } catch (e) {
      const name = e instanceof DOMException ? e.name : e instanceof Error ? e.name : ''
      const msg = e instanceof Error ? e.message : String(e)
      const low = `${name} ${msg}`.toLowerCase()
      if (low.includes('permissions policy') || low.includes('disallowed') || name === 'SecurityError') {
        setPortResult({
          text: `⛔ ЗАБЛОКИРОВАНО Permissions-Policy фрейма: ${name}: ${msg}. COM-сканер здесь работать не будет — нужен гибрид (отдельная вкладка).`,
          ok: false,
        })
      } else if (low.includes('no port selected') || name === 'NotFoundError') {
        setPortResult({ text: `Диалог закрыт без выбора (${name}). API доступен — повторите и выберите порт.`, ok: null })
      } else {
        setPortResult({ text: `Прочая ошибка: ${name}: ${msg}`, ok: null })
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <details style={styles.details}>
      <summary style={styles.summary}>🔧 Диагностика COM-сканера в этом окне</summary>
      <div style={styles.body}>
        <button type="button" style={styles.btn} onClick={() => void runProbes()}>
          Проверить окружение
        </button>
        <button type="button" style={styles.btn} disabled={busy} onClick={() => void requestPort()}>
          Запросить COM-порт (requestPort)
        </button>

        {rows && (
          <table style={styles.table}>
            <tbody>
              {rows.map((r) => (
                <tr key={r.label}>
                  <td style={styles.tdLabel}>{r.label}</td>
                  <td style={{ ...styles.tdValue, color: r.ok === false ? '#b91c1c' : r.ok === true ? '#15803d' : '#374151' }}>
                    {r.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {portResult && (
          <div
            style={{
              ...styles.result,
              color: portResult.ok === false ? '#b91c1c' : portResult.ok === true ? '#15803d' : '#374151',
              borderColor: portResult.ok === false ? '#fecaca' : portResult.ok === true ? '#bbf7d0' : '#e5e7eb',
            }}
          >
            {portResult.text}
          </div>
        )}

        <div style={styles.hint}>
          Сравните результат здесь (внутри iframe МС) и на skandata.ru, открытом напрямую в отдельной вкладке.
        </div>
      </div>
    </details>
  )
}

const styles: Record<string, CSSProperties> = {
  details: {
    margin: '8px 20px',
    border: '1px dashed var(--ms-border-light)',
    borderRadius: 'var(--r-md)',
    background: 'var(--ms-bg)',
    fontSize: 12,
  },
  summary: { cursor: 'pointer', padding: '8px 12px', fontWeight: 600, color: 'var(--ms-text)' },
  body: { padding: '4px 12px 12px', display: 'flex', flexDirection: 'column', gap: 8 },
  btn: {
    alignSelf: 'flex-start',
    background: 'var(--ms-bg-alt)',
    border: '1px solid var(--ms-border-light)',
    borderRadius: 'var(--r-sm)',
    padding: '6px 12px',
    cursor: 'pointer',
    fontSize: 12,
  },
  table: { borderCollapse: 'collapse', width: '100%' },
  tdLabel: { padding: '3px 8px 3px 0', color: 'var(--ms-text-muted)', whiteSpace: 'nowrap', verticalAlign: 'top' },
  tdValue: { padding: '3px 0', fontFamily: 'monospace', wordBreak: 'break-word' },
  result: { padding: 8, border: '1px solid', borderRadius: 'var(--r-sm)', lineHeight: 1.4 },
  hint: { color: 'var(--ms-text-subtle)', fontSize: 11 },
}
