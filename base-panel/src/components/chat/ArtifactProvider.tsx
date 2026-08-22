'use client'

import * as React from 'react'

type ArtifactContext = {
  /** The code currently shown in the side panel, or null when it is closed. */
  code: string | null
  /** Highlight.js language hint (`json`, `bash`, …). Empty when the fence had none. */
  lang: string
  open: (code: string, lang: string) => void
  close: () => void
}

const Ctx = React.createContext<ArtifactContext | null>(null)

/** Long fenced blocks open beside the transcript instead of inside it. State lives here so the
 *  panel is a sibling column of the chat — opening it never remounts the message list. */
export function ArtifactProvider({
  conversationUuid,
  children,
}: {
  /** The conversation on screen. When it changes the panel is showing code from a transcript
   *  that is no longer there, so it closes. */
  conversationUuid: string | null
  children: React.ReactNode
}) {
  const [current, setCurrent] = React.useState<{ code: string; lang: string } | null>(null)

  const open = React.useCallback((code: string, lang: string) => setCurrent({ code, lang }), [])
  const close = React.useCallback(() => setCurrent(null), [])

  // `null → uuid` is the first turn of a new chat naming itself, not a switch: closing there would
  // yank the panel out from under someone who opened a card mid-stream.
  const previous = React.useRef(conversationUuid)
  React.useEffect(() => {
    if (previous.current !== conversationUuid && previous.current !== null) setCurrent(null)
    previous.current = conversationUuid
  }, [conversationUuid])

  const value = React.useMemo(
    () => ({ code: current?.code ?? null, lang: current?.lang ?? '', open, close }),
    [current, open, close],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

/** null outside the chat page — the wizards have no panel, so their long code stays inline. */
export function useArtifact(): ArtifactContext | null {
  return React.useContext(Ctx)
}
