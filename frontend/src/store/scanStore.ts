import { create } from 'zustand'
import type { Scan, Document } from '../api/client'

interface Stats {
  valid: number
  invalid: number
  duplicate: number
  pending: number
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
}

function calcStats(scans: Scan[]): Stats {
  return scans.reduce(
    (acc, s) => {
      acc[s.status] = (acc[s.status] || 0) + 1
      return acc
    },
    { valid: 0, invalid: 0, duplicate: 0, pending: 0 } as Stats
  )
}

export const useScanStore = create<ScanStore>((set) => ({
  document: null,
  scans: [],
  stats: { valid: 0, invalid: 0, duplicate: 0, pending: 0 },
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
      stats: { valid: 0, invalid: 0, duplicate: 0, pending: 0 },
      czTokenExpired: false,
    }),
}))
