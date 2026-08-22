'use client'

import * as React from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { useConversations } from '@/hooks/useConversations'

/** Anything else in that slot cannot name a conversation, so it opens a blank chat rather
 *  than firing a request the API can only 404 — and it keeps a hand-typed path out of the
 *  URL that `apiFetch` builds. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** `/chat/<uuid>` → the uuid; `/chat` and every other route → nothing open. */
function uuidFromPath(pathname: string): string | null {
  const segment = pathname.startsWith('/chat/') ? pathname.slice('/chat/'.length).split('/')[0] : ''
  return UUID.test(segment) ? segment : null
}

type ConversationsContext = ReturnType<typeof useConversations> & {
  /** The conversation the chat page should be showing, read from the URL. */
  activeUuid: string | null
  /** Open one, or `null` for a blank chat. `replace` is for a thread the user never
   *  navigated to — one a turn just created — so Back steps over it. */
  setActiveUuid: (uuid: string | null, options?: { replace?: boolean }) => void
}

const Ctx = React.createContext<ConversationsContext | null>(null)

/** History lives in the sidebar but is driven by the chat page, so the list is fetched once
 *  here. The SELECTION is not state: it is derived from the route, which is the only way a
 *  deep link, a refresh and the back button can agree on which conversation is open. */
export function ConversationsProvider({ children }: { children: React.ReactNode }) {
  const conversations = useConversations()
  const router = useRouter()
  const pathname = usePathname()
  const activeUuid = uuidFromPath(pathname)
  const { remove } = conversations

  // Read through a ref so both callbacks stay stable across navigations: the chat page has
  // an effect keyed on `setActiveUuid`, and a new identity per route would re-run it — and
  // re-fetch the conversation list — on every navigation it just caused.
  const pathnameRef = React.useRef(pathname)
  pathnameRef.current = pathname

  const setActiveUuid = React.useCallback(
    (uuid: string | null, options?: { replace?: boolean }) => {
      const href = uuid ? `/chat/${uuid}` : '/chat'
      if (href === pathnameRef.current) return
      if (options?.replace) router.replace(href)
      else router.push(href)
    },
    [router],
  )

  // deleting the open conversation must also close it, or the page keeps rendering a dead thread
  const removeAndDeselect = React.useCallback(
    async (uuid: string) => {
      if (uuid === uuidFromPath(pathnameRef.current)) router.replace('/chat')
      await remove(uuid)
    },
    [remove, router],
  )

  const value = React.useMemo(
    () => ({ ...conversations, remove: removeAndDeselect, activeUuid, setActiveUuid }),
    [conversations, removeAndDeselect, activeUuid, setActiveUuid],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useConversationsContext() {
  const ctx = React.useContext(Ctx)
  if (!ctx) throw new Error('useConversationsContext must be used inside <ConversationsProvider>')
  return ctx
}
