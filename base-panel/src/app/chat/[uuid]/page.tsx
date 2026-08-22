import { ChatView } from '@/components/chat/ChatView'

/** One conversation, addressable. The uuid is read from the URL by ConversationsProvider,
 *  so this route renders exactly what /chat does — no second loading path to keep in sync. */
export default function ChatConversationPage() {
  return <ChatView />
}
