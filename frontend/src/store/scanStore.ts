import { create } from 'zustand'
import type { Scan, Document, PlanItem } from '../api/client'

interface Stats {
  valid: number
  invalid: number
  duplicate: number
  pending: number
  overflow: number
}

export interface PlanProgress {
  byGtin: Record<string, { scanned: number; expected: number; product_name: string }>
  total: { scanned: number; expected: number }
  hasPlan: boolean
}

interface ScanStore {
  document: Document | null
  scans: Scan[]
  stats: Stats
  czTokenExpired: boolean

  setDocument: (doc: Document | null) => void
  setScans: (scans: Scan[]) => void
  addScan: (scan: Scan) => void
  updateScan: (id: string, update: Partial<Scan>) => void
  removeScan: (id: string) => void
  setCzTokenExpired: (v: boolean) => void
  reset: () => void
  getProgress: () => PlanProgress
}

function calcStats(scans: Scan[]): Stats {
  return scans.reduce(
    (acc, s) => {
      acc[s.status] = (acc[s.status] || 0) + 1
      return acc
    },
    { valid: 0, invalid: 0, duplicate: 0, pending: 0, overflow: 0 } as Stats
  )
}

export function buildProgress(plan: PlanItem[] | undefined, scans: Scan[]): PlanProgress {
  const items = plan ?? []
  if (items.length === 0) {
    return { byGtin: {}, total: { scanned: 0, expected: 0 }, hasPlan: false }
  }
  const byGtin: PlanProgress['byGtin'] = {}
  let totalExpected = 0
  for (const p of items) {
    if (!p.gtin) continue
    byGtin[p.gtin] = {
      scanned: 0,
      expected: p.expected_qty,
      product_name: p.product_name,
    }
    totalExpected += p.expected_qty
  }
  let totalScanned = 0
  for (const s of scans) {
    if (s.status !== 'valid' || !s.gtin) continue
    const slot = byGtin[s.gtin]
    if (!slot) continue
    if (slot.scanned < slot.expected) {
      slot.scanned += 1
      totalScanned += 1
    }
  }
  return { byGtin, total: { scanned: totalScanned, expected: totalExpected }, hasPlan: true }
}

export const useScanStore = create<ScanStore>((set, get) => ({
  document: null,
  scans: [],
  stats: { valid: 0, invalid: 0, duplicate: 0, pending: 0, overflow: 0 },
  czTokenExpired: false,

  setDocument: (doc) => set({ document: doc }),

  setScans: (scans) => set({ scans, stats: calcStats(scans) }),

  addScan: (scan) =>
    set((state) => {
      const scans = [scan, ...state.scans]
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

  setCzTokenExpired: (v) => set({ czTokenExpired: v }),

  reset: () =>
    set({
      document: null,
      scans: [],
      stats: { valid: 0, invalid: 0, duplicate: 0, pending: 0, overflow: 0 },
      czTokenExpired: false,
    }),

  getProgress: () => {
    const { document, scans } = get()
    return buildProgress(document?.plan, scans)
  },
}))
