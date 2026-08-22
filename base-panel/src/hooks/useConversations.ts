'use client'

import { useCallback, useEffect, useState } from 'react'
import { apiFetch, type ConversationSummary } from '@/lib/api'
import { useMe } from '@/components/auth/MeProvider'

/** The signed-in user's conversation list, for the continuity switcher. */
export function useConversations() {
  const { me, loading: loadingMe } = useMe()
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch<ConversationSummary[] | { items?: ConversationSummary[] }>(
        '/api/v1/conversations',
      )
      setItems(Array.isArray(data) ? data : (data?.items ?? []))
      setError(null)
    } catch (err) {
      setError((err as Error)?.message || 'فهرست گفت‌وگوها بارگذاری نشد.')
    } finally {
      setLoading(false)
    }
  }, [])

  const remove = useCallback(async (uuid: string) => {
    // Optimistic, but the row goes back if the server refused — a conversation that looks
    // deleted while it still exists is worse than a visible failure.
    let previous: ConversationSummary[] = []
    setItems((prev) => {
      previous = prev
      return prev.filter((c) => c.uuid !== uuid)
    })
    try {
      await apiFetch<void>(`/api/v1/conversations/${uuid}`, { method: 'DELETE' })
    } catch (err) {
      setItems(previous)
      setError((err as Error)?.message || 'حذف گفت‌وگو ناموفق بود.')
    }
  }, [])

  const rename = useCallback(async (uuid: string, title: string) => {
    const wanted = title.trim()
    if (!wanted) return
    // Optimistic, then corrected by the server's own copy: it trims, strips and clamps, so
    // the sidebar must show what was actually stored rather than what was typed.
    let previous: ConversationSummary[] = []
    setItems((prev) => {
      previous = prev
      return prev.map((c) => (c.uuid === uuid ? { ...c, title: wanted } : c))
    })
    try {
      const saved = await apiFetch<ConversationSummary>(`/api/v1/conversations/${uuid}`, {
        method: 'PATCH',
        body: JSON.stringify({ title: wanted }),
      })
      setItems((prev) => prev.map((c) => (c.uuid === uuid ? { ...c, title: saved.title } : c)))
      setError(null)
    } catch (err) {
      setItems(previous)
      setError((err as Error)?.message || 'نام گفت‌وگو ذخیره نشد.')
    }
  }, [])

  // Keyed on the uuid, not the `me` object: `refresh()` mints a new object for the same
  // person, and depending on it would refetch the whole list on every sign-in probe.
  const uid = me?.uuid ?? null

  useEffect(() => {
    if (loadingMe) return
    if (!uid) {
      // `loading` starts true, so without setting it false here ConversationList sits on
      // «در حال بارگذاری…» forever for a signed-out visitor.
      setItems([])
      setError(null)
      setLoading(false)
      return
    }
    void refresh()
  }, [refresh, uid, loadingMe])

  return { items, loading, error, refresh, remove, rename }
}
