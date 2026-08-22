'use client'

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

/** The signed-in operator, as `/api/v1/auth/me` returns them. */
export type Me = {
  uuid: string
  username: string
  displayName: string | null
  /** The stored flag. `role` is the same thing spelled out, sent for readability. */
  isSuperuser: boolean
  role: 'admin' | 'superuser'
  isActive: boolean
}

type MeState = {
  me: Me | null
  /** True until the first `/me` call settles, so nothing flashes signed-out then signed-in. */
  loading: boolean
  /** Re-reads `/me` and hands back the result, so a caller (login) can act on it directly
   *  instead of racing the state update. */
  refresh: () => Promise<Me | null>
  signOut: () => Promise<void>
}

const MeContext = createContext<MeState>({
  me: null,
  loading: true,
  refresh: async () => null,
  signOut: async () => {},
})

/**
 * Holds who is signed in. Every route but the login and register pages needs an account.
 *
 * A 401 here is the ordinary signed-out case, not an error, so it sets `me` to null and
 * nothing else happens — `AppShell` is the one place that turns "nobody is signed in" into a
 * redirect, exactly once. What this context decides is only what the panel *shows*; what a
 * visitor may actually *do* is refused by the server on every request.
 */
export function MeProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async (): Promise<Me | null> => {
    try {
      const user = await apiFetch<Me>('/api/v1/auth/me')
      setMe(user)
      return user
    } catch {
      setMe(null)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  const signOut = useCallback(async () => {
    try {
      // An empty JSON body, deliberately: it makes the request non-simple, so the browser
      // preflights it and no other site can sign an operator out.
      await apiFetch('/api/v1/auth/logout', { method: 'POST', body: '{}' })
    } catch {
      // Logout is idempotent server-side and clears the cookie either way; a failure here
      // must still take the user out of the operator UI rather than stranding them in it.
    }
    setMe(null)
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return (
    <MeContext.Provider value={{ me, loading, refresh, signOut }}>{children}</MeContext.Provider>
  )
}

/** The signed-in operator, or null. */
export function useMe(): MeState {
  return useContext(MeContext)
}
