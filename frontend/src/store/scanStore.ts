import { create } from 'zustand'
import type { Scan, Document, PlanItem } from '../api/client'

interface Stats {
  valid: number
  invalid: number
  duplicate: number
  pending: number
  scanned: number
  overflow: number
  unknown_product: number
  used_in_other_doc: number
}

/** Статусы, которые оптимистично считаем «добавленными» в документ до пакетной проверки:
 * valid/overflow — подтверждены ЧЗ, scanned — принят локально, ждёт проверки. */
const ADDED_STATUSES = ['valid', 'overflow', 'scanned'] as const
function isAdded(status: string): boolean {
  return (ADDED_STATUSES as readonly string[]).includes(status)
}

/** Одна строка сводки: товар (GTIN) и сколько кодов добавлено. */
export interface ProgressRow {
  gtin: string
  /** UUID товара в МС из плана (для ручного выбора строки). */
  product_id: string | null
  product_name: string
  expected: number
  /** Сколько сканов в статусе valid (в рамках плана; для футера «в плане»). */
  scanned: number
  /** valid + overflow по этому GTIN — всё, что уйдёт в отгрузку по строке. */
  addedTotal: number
  /** Только свободная сборка: сколько ещё на проверке. */
  pendingCount?: number
  /** Немаркированный товар (собирается сканом штрихкода). Только для строк плана. */
  unmarked?: boolean
}

/** Выбранная позиция для взаимной подсветки позиция↔коды (по product_id и/или GTIN). */
export interface PositionSelection {
  productId: string | null
  gtinKey: string | null
}

/** Позиция, отсканированная НЕ по плану (её GTIN/товар нет ни в одной строке плана). */
export interface OffPlanRow {
  gtin: string
  product_name: string
  /** valid + overflow единиц (уйдут в отгрузку, если не удалить). */
  addedTotal: number
  pendingCount?: number
  /** id сканов этой позиции — для удаления пачкой. */
  scanIds: string[]
}

export interface PlanProgress {
  /** Есть план из МС (ожидаемые количества по GTIN). */
  hasPlan: boolean
  /** Показывать блок сводки (план или свободная группировка по сканам). */
  hasSummary: boolean
  rows: ProgressRow[]
  /** Позиции, отсканированные не по плану (только когда есть план). */
  offPlanRows: OffPlanRow[]
  total: {
    scanned: number
    expected: number
    addedTotal: number
  }
}

interface ScanStore {
  document: Document | null
  scans: Scan[]
  stats: Stats
  /** Явный товар МС для следующих сканов (UUID); null = только авто по GTIN/плану. */
  targetProductId: string | null
  setTargetProductId: (id: string | null) => void
  /** Активная позиция для взаимной подсветки позиция↔коды; объединена с targetProductId. */
  selection: PositionSelection | null
  setSelection: (sel: PositionSelection | null) => void
  /** Переключить выбор позиции: повторный клик по той же позиции снимает подсветку. */
  togglePositionSelection: (sel: PositionSelection) => void
  /** Режим удаления: следующий отсканированный код будет удалён из списка, а не добавлен. */
  deleteMode: boolean
  setDeleteMode: (v: boolean) => void
  /** Как обрабатывать SSCC-короб: true — раскрывать на штучные КМ, false — целиком (transportpack). */
  unpackBox: boolean
  setUnpackBox: (v: boolean) => void
  /** Истёк/отсутствует токен ЧЗ — нужен вход для распознавания кодов (баннер). */
  czTokenExpired: boolean
  setCzTokenExpired: (v: boolean) => void
  /** id строки для кратковременной подсветки (повторный скан существующего кода). */
  flashScanId: string | null
  /** Подсветить строку скана: выставить id и через ~1.2с сбросить. */
  flashScan: (id: string) => void
  /** Идёт пакетная проверка марок в ЧЗ (между «Проверить марки» и событием verify_done). */
  verifying: boolean
  setVerifying: (v: boolean) => void
  /** Результат списания (вывод из оборота ЧЗ): приходит по WS после опроса статуса. */
  writeoffResult: { status: 'done' | 'error'; error?: string | null } | null
  setWriteoffResult: (v: { status: 'done' | 'error'; error?: string | null } | null) => void

  setDocument: (doc: Document | null) => void
  setScans: (scans: Scan[]) => void
  addScan: (scan: Scan) => void
  updateScan: (id: string, update: Partial<Scan>) => void
  removeScan: (id: string) => void
  reset: () => void
  getProgress: () => PlanProgress
}

function calcStats(scans: Scan[]): Stats {
  return scans.reduce(
    (acc, s) => {
      acc[s.status] = (acc[s.status] || 0) + 1
      return acc
    },
    { valid: 0, invalid: 0, duplicate: 0, pending: 0, scanned: 0, overflow: 0, unknown_product: 0, used_in_other_doc: 0 } as Stats
  )
}

/** Единый GTIN-14 для сравнения плана и scan.gtin (как на бэкенде normalize_gtin_key). */
export function normalizeGtinKey(g: string | number | null | undefined): string | null {
  if (g == null || g === '') return null
  const d = String(g).replace(/\D/g, '')
  if (!d) return null
  if (d.length > 14) return d.slice(-14)
  if (d.length === 14) return d
  if (d.length === 13) return d.padStart(14, '0')
  return d.padStart(14, '0')
}

/** GTIN для прогресса: поле скана или лёгкий разбор сырой CIS без пересборки строки (фоллбек для UI). */
export function effectiveGtinKey(scan: Scan): string | null {
  const k = normalizeGtinKey(scan.gtin)
  if (k) return k
  const c = scan.code.trim()
  if (c.startsWith('01') && c.length >= 16) {
    const chunk = c.slice(2, 16)
    if (/^\d{14}$/.test(chunk)) return normalizeGtinKey(chunk)
  }
  const lead = c.slice(0, 14)
  if (/^\d{14}$/.test(lead)) return normalizeGtinKey(lead)
  return null
}

/** Сколько единиц товара представляет скан: короб/блок = box_quantity, обычный = 1. */
export function scanUnits(scan: Scan): number {
  if (scan.box_quantity != null) return scan.box_quantity
  return 1
}

export type OwnerCheck = 'mismatch' | 'unknown'

/** Сверка владельца марки (owner_inn из ЧЗ) с владельцем подписи (signatureInn = cz_inn)
 * в отгрузке. Возвращает:
 *  - 'mismatch' — ИНН владельца марки не совпадает с владельцем подписи;
 *  - 'unknown'  — ЧЗ не вернул ИНН владельца (нет токена/mock/пусто);
 *  - null       — проверка неприменима (немаркированный штрихкод, статус не valid/overflow,
 *                 нет ИНН подписи) или владелец совпал.
 * Никогда не блокирует отгрузку — только для подсветки. */
export function ownerCheckState(
  scan: Scan,
  signatureInn: string | null | undefined,
): OwnerCheck | null {
  if (scan.is_barcode) return null
  if (!signatureInn) return null
  if (scan.status !== 'valid' && scan.status !== 'overflow') return null
  const inn = (scan.owner_inn ?? '').trim()
  if (!inn) return 'unknown'
  return inn === signatureInn.trim() ? null : 'mismatch'
}

function sumUnits(scans: Scan[]): number {
  return scans.reduce((a, s) => a + scanUnits(s), 0)
}

function scanMatchesPlanRow(
  scan: Scan,
  planKey: string | null,
  planProductId: string | null | undefined,
): boolean {
  const byPid =
    Boolean(planProductId && scan.moysklad_product_id) &&
    scan.moysklad_product_id === planProductId
  const byGtin = Boolean(planKey && effectiveGtinKey(scan) === planKey)
  return byPid || byGtin
}

/** Скан принадлежит выбранной позиции (для подсветки кодов). */
export function selectionMatchesScan(sel: PositionSelection, scan: Scan): boolean {
  return scanMatchesPlanRow(scan, sel.gtinKey, sel.productId)
}

/** Строка прогресса соответствует выбранной позиции (для подсветки позиции). */
export function selectionMatchesRow(sel: PositionSelection, row: ProgressRow): boolean {
  const byPid = Boolean(sel.productId && row.product_id) && row.product_id === sel.productId
  const byGtin = Boolean(sel.gtinKey && normalizeGtinKey(row.gtin) === sel.gtinKey)
  return byPid || byGtin
}

/** Две позиции — одна и та же (по product_id или по GTIN). */
function sameSelection(a: PositionSelection | null, b: PositionSelection): boolean {
  if (!a) return false
  const byPid = Boolean(a.productId && b.productId) && a.productId === b.productId
  const byGtin = Boolean(a.gtinKey && b.gtinKey) && a.gtinKey === b.gtinKey
  return byPid || byGtin
}

function groupKey(scan: Scan): string {
  return scan.gtin || scan.code.slice(0, 32) || 'unknown'
}

export function buildProgress(plan: PlanItem[] | undefined, scans: Scan[]): PlanProgress {
  const planItems = (plan ?? []).filter((p) => p.gtin || p.product_id)

  if (planItems.length > 0) {
    const rows: ProgressRow[] = planItems.map((p) => {
      const gtin = (p.gtin as string) || ''
      const planKey = p.gtin ? normalizeGtinKey(p.gtin) : null
      const planProductId = (p.product_id as string) || null
      const addedTotal = sumUnits(
        scans.filter(
          (s) => isAdded(s.status) && scanMatchesPlanRow(s, planKey, planProductId),
        ),
      )
      const scanned = sumUnits(
        scans.filter(
          (s) =>
            s.status === 'valid' && scanMatchesPlanRow(s, planKey, planProductId),
        ),
      )
      return {
        gtin: gtin || planProductId || '—',
        product_id: planProductId,
        product_name: p.product_name,
        expected: p.expected_qty,
        scanned,
        addedTotal,
        unmarked: p.marked === false,
      }
    })
    const total = {
      scanned: rows.reduce((a, r) => a + r.scanned, 0),
      expected: rows.reduce((a, r) => a + r.expected, 0),
      addedTotal: rows.reduce((a, r) => a + r.addedTotal, 0),
    }

    // Позиции не из плана: сканы valid/overflow/pending, чей GTIN/товар не совпал ни
    // с одной строкой плана. unknown_product сюда не берём — это отдельный поток подбора.
    const planRefs = planItems.map((p) => ({
      key: p.gtin ? normalizeGtinKey(p.gtin) : null,
      pid: (p.product_id as string) || null,
    }))
    const inPlan = (s: Scan): boolean =>
      planRefs.some((r) => scanMatchesPlanRow(s, r.key, r.pid))
    const offGroups = new Map<string, OffPlanRow>()
    for (const s of scans) {
      if (!['valid', 'overflow', 'pending', 'scanned'].includes(s.status)) continue
      if (inPlan(s)) continue
      const k = groupKey(s)
      let g = offGroups.get(k)
      if (!g) {
        g = { gtin: s.gtin || k, product_name: '', addedTotal: 0, scanIds: [] }
        offGroups.set(k, g)
      }
      g.scanIds.push(s.id)
      if (!g.product_name && s.product_name) g.product_name = s.product_name
      if (s.status === 'pending') g.pendingCount = (g.pendingCount ?? 0) + scanUnits(s)
      else g.addedTotal += scanUnits(s)
    }
    const offPlanRows = Array.from(offGroups.values())
      .map((g) => ({ ...g, product_name: g.product_name || g.gtin }))
      .sort((a, b) => b.addedTotal - a.addedTotal)

    return {
      hasPlan: true,
      hasSummary: true,
      rows,
      offPlanRows,
      total,
    }
  }

  // Свободная сборка: группируем по GTIN (или по фрагменту кода, если GTIN нет).
  const groups = new Map<
    string,
    { gtin: string; productNames: string[]; list: Scan[] }
  >()
  for (const s of scans) {
    if (!['valid', 'overflow', 'pending', 'scanned'].includes(s.status)) continue
    const k = groupKey(s)
    let g = groups.get(k)
    if (!g) {
      g = { gtin: s.gtin || k, productNames: [], list: [] }
      groups.set(k, g)
    }
    g.list.push(s)
    if (s.product_name) g.productNames.push(s.product_name)
  }

  const rows: ProgressRow[] = Array.from(groups.values())
    .map((g) => {
      const addedTotal = sumUnits(g.list.filter((s) => isAdded(s.status)))
      const scanned = addedTotal
      const pendingCount = sumUnits(g.list.filter((s) => s.status === 'pending'))
      const displayName = g.productNames[0] || g.gtin || 'Без GTIN'
      return {
        gtin: g.gtin,
        product_id: null,
        product_name: displayName,
        expected: 0,
        scanned,
        addedTotal,
        pendingCount: pendingCount > 0 ? pendingCount : undefined,
      }
    })
    .sort((a, b) =>
      (a.product_name || a.gtin).localeCompare(b.product_name || b.gtin, 'ru')
    )

  const total = {
    scanned: rows.reduce((a, r) => a + r.scanned, 0),
    expected: 0,
    addedTotal: rows.reduce((a, r) => a + r.addedTotal, 0),
  }

  return {
    hasPlan: false,
    hasSummary: rows.length > 0,
    rows,
    offPlanRows: [],
    total,
  }
}

export const useScanStore = create<ScanStore>((set, get) => ({
  document: null,
  scans: [],
  stats: { valid: 0, invalid: 0, duplicate: 0, pending: 0, scanned: 0, overflow: 0, unknown_product: 0, used_in_other_doc: 0 },
  targetProductId: null,
  selection: null,
  deleteMode: false,
  unpackBox: true,
  czTokenExpired: false,
  flashScanId: null,
  verifying: false,
  writeoffResult: null,

  setVerifying: (v) => set({ verifying: v }),
  setWriteoffResult: (v) => set({ writeoffResult: v }),
  setTargetProductId: (id) => set({ targetProductId: id }),
  setSelection: (sel) => set({ selection: sel }),
  togglePositionSelection: (sel) =>
    set((state) =>
      sameSelection(state.selection, sel)
        ? { selection: null, targetProductId: null }
        : { selection: sel, targetProductId: sel.productId },
    ),
  setDeleteMode: (v) => set({ deleteMode: v }),
  setUnpackBox: (v) => set({ unpackBox: v }),
  setCzTokenExpired: (v) => set({ czTokenExpired: v }),
  flashScan: (id) => {
    set({ flashScanId: id })
    setTimeout(() => {
      // Сбрасываем только если за это время не подсветили другую строку.
      if (get().flashScanId === id) set({ flashScanId: null })
    }, 1200)
  },

  setDocument: (doc) => set({ document: doc, targetProductId: null, selection: null, deleteMode: false }),

  setScans: (scans) => set({ scans, stats: calcStats(scans) }),

  addScan: (scan) =>
    set((state) => {
      // Upsert по id: повторный скан штрихкода немаркированного товара возвращает тот
      // же скан с обновлённым box_quantity — заменяем строку, а не плодим дубль.
      const idx = state.scans.findIndex((s) => s.id === scan.id)
      const scans =
        idx >= 0
          ? state.scans.map((s) => (s.id === scan.id ? scan : s))
          : [scan, ...state.scans]
      return { scans, stats: calcStats(scans) }
    }),

  updateScan: (id, update) =>
    set((state) => {
      const scans = state.scans.map((s) => (s.id === id ? { ...s, ...update } : s))
      return { scans, stats: calcStats(scans) }
    }),

  removeScan: (id) =>
    set((state) => {
      const scans = state.scans.filter((s) => s.id !== id)
      return { scans, stats: calcStats(scans) }
    }),

  reset: () =>
    set({
      document: null,
      scans: [],
      stats: { valid: 0, invalid: 0, duplicate: 0, pending: 0, scanned: 0, overflow: 0, unknown_product: 0, used_in_other_doc: 0 },
      targetProductId: null,
      selection: null,
      deleteMode: false,
      verifying: false,
    }),

  getProgress: () => {
    const { document, scans } = get()
    return buildProgress(document?.plan, scans)
  },
}))
