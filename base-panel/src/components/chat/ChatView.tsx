'use client'

import { useEffect } from 'react'
import { PageContainer } from '@/components/layout/PageContainer'
import { ArtifactPanel } from '@/components/chat/ArtifactPanel'
import { ArtifactProvider } from '@/components/chat/ArtifactProvider'
import { Composer } from '@/components/chat/Composer'
import { useConversationsContext } from '@/components/chat/ConversationsProvider'
import { MessageList } from '@/components/chat/MessageList'
import { useChat } from '@/hooks/useChat'

/** The chat screen. Both `/chat` and `/chat/<uuid>` render this one component so the two
 *  routes cannot drift; WHICH conversation is open is read from the URL by the provider,
 *  which is what makes a deep link, a refresh and the back button all agree. */
export function ChatView() {
  const { state, send, stop, reset, retry, editMessage, switchVersion, loadConversation } = useChat()
  const { activeUuid, setActiveUuid, refresh } = useConversationsContext()
  const { streaming, conversationUuid } = state

  // a finished turn may have created the conversation or changed its title
  useEffect(() => {
    if (!streaming && conversationUuid) {
      // `replace`, not push: the user never navigated to this conversation — the turn they
      // typed on /chat created it — so Back must return to wherever they came from rather
      // than step through a URL that did not exist when they arrived.
      setActiveUuid(conversationUuid, { replace: true })
      void refresh()
    }
  }, [streaming, conversationUuid, refresh, setActiveUuid])

  // The URL owns the selection now, so it arrives from there: open it, or — when it clears —
  // start fresh. conversationUuid is read but not a dep on purpose: this effect reacts to the
  // route, and re-running it on our own load would re-fetch what we just loaded.
  useEffect(() => {
    if (activeUuid && activeUuid !== conversationUuid) void loadConversation(activeUuid)
    if (activeUuid === null && conversationUuid) reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeUuid])

  return (
    // Two columns: the conversation, and the artifact panel — which renders nothing until a long
    // code block is opened, and in RTL takes the inline-end edge by flex order alone.
    <ArtifactProvider conversationUuid={activeUuid}>
      <div className="flex h-full min-h-0 gap-4">
        {/* Full width: the transcript still reads at ~75 characters a line, but the column is
            centred inside the scroller rather than being the scroller — see MessageList. */}
        <PageContainer size="full" className="h-full min-h-0 flex-1">
          <MessageList
            messages={state.messages}
            streaming={streaming}
            loading={state.loading}
            toolStatus={state.toolStatus}
            error={state.error}
            suggestions={state.suggestions}
            onSend={send}
            onRetry={retry}
            onEdit={editMessage}
            onSwitchVersion={switchVersion}
          />

          <Composer onSend={send} onStop={stop} streaming={streaming} />
        </PageContainer>

        <ArtifactPanel />
      </div>
    </ArtifactProvider>
  )
}
