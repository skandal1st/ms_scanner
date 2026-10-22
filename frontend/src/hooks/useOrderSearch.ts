import { useEffect, useState } from 'react'

/** Wait for typing to pause before searching order history. */
export function useOrderSearch(value: string) {
  const query = value.trim()
  const [search, setSearch] = useState(query)
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query), 400)
    return () => window.clearTimeout(timer)
  }, [query])
  return search
}

export function orderSearchPage<T>(response: { data: T[]; headers: Record<string, unknown> }) {
  const cursor = response.headers['x-next-offset']
  return { rows: response.data, nextCursor: cursor === '' || cursor == null ? undefined : Number(cursor) }
}
