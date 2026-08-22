'use client'

import { useEffect, useRef, useState } from 'react'
import type { ChatMessage } from '@/hooks/useChat'
import { MessageEditor } from './MessageEditor'
import { MessageVersions } from './MessageVersions'
import { Markdown } from './Markdown'
import { MessageActions } from './MessageActions'
import { SourcesList } from './SourcesList'

/** `۰۸:۱۴` in Persian digits, or nothing when the turn has no timestamp yet.
 *
 *  Clock only, no date: MessageList already draws a «۳۰ مرداد» divider whenever the day
 *  changes, so repeating it under every answer says nothing the row above did not. */
function formatTime(iso?: string): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })
}

function Caret() {
  return <span className="inline-block h-4 w-2 animate-pulse rounded-sm bg-muted-foreground align-middle" />
}

/** One turn. User text stays plain; assistant text is markdown with citations. */
export function MessageBubble({
  message,
  onRetry,
  onEdit,
  onSwitchVersion,
  busy,
}: {
  message: ChatMessage
  onRetry?: () => void
  onEdit?: (uuid: string, content: string) => void
  onSwitchVersion?: (uuid: string, versionIndex: number) => void
  busy?: boolean
}) {
  const [editing, setEditing] = useState(false)
  // hovering a [n] in the answer lights its source card, and the other way round
  const [activeSource, setActiveSource] = useState<number | null>(null)
  const bodyRef = useRef<HTMLDivElement>(null)

  // Paint the matching [n] by hand: passing it into <Markdown> would rebuild the component
  // overrides — i.e. the element types — and remount the whole answer on every hover.
  useEffect(() => {
    const nodes = bodyRef.current?.querySelectorAll<HTMLElement>('[data-cite]')
    nodes?.forEach((node) => node.classList.toggle('cite-active', node.dataset.cite === String(activeSource)))
  }, [activeSource, message.content])
  const isUser = message.role === 'user'
  const empty = !message.content.trim()
  // Answers only. You know when you typed your own question; the timestamp that carries
  // information is the one on the reply, and stamping both just doubles the clutter under
  // every exchange.
  const time = isUser ? '' : formatTime(message.createdAt)

  // Same row for both roles, flush with the column's start edge. The old `-ms-1` pushed the
  // <time> 4px past that edge, where the scroller clipped it; `flex-row-reverse px-2` on the
  // user side moved it for no gain. The column's own alignment already places it.
  const index = message.versionIndex ?? 1
  const count = message.versionCount ?? 1
  const versions = onSwitchVersion ? (
    <MessageVersions
      index={index}
      count={count}
      disabled={busy}
      onPrev={() => onSwitchVersion(message.uuid, index - 1)}
      onNext={() => onSwitchVersion(message.uuid, index + 1)}
    />
  ) : null

  const meta = (
    // `w-full` + `ms-auto` on the <time>, not just reordering: the row is only as wide as
    // its contents, so moving the timestamp last would merely park it beside the icons.
    // The auto inline-start margin eats every pixel between them, so the clock lands at the
    // column's inline-end edge — bottom-LEFT in this RTL layout — while the icon cluster
    // stays at the start edge. `me-2` matches the 8px an icon glyph is already inset by
    // inside its 32px button, so the two ends of the row read as equally indented rather
    // than the clock alone being flush against the edge.
    <div className="flex w-full items-center gap-1">
      {versions}
      {!message.pending && !empty ? (
        <MessageActions
          text={message.content}
          onRetry={onRetry}
          retryLabel={isUser ? 'پاسخ دوباره' : 'تولید دوبارهٔ پاسخ'}
          onEdit={isUser && onEdit ? () => setEditing(true) : undefined}
        />
      ) : null}
      {time ? (
        <time dateTime={message.createdAt} className="ms-auto me-2 text-[11px] text-muted-foreground">
          {time}
        </time>
      ) : null}
    </div>
  )

  if (isUser) {
    if (editing && onEdit) {
      return (
        <MessageEditor
          initial={message.content}
          onCancel={() => setEditing(false)}
          onSubmit={(text) => {
            setEditing(false)
            onEdit(message.uuid, text)
          }}
        />
      )
    }
    return (
      <div className="group flex flex-col items-start gap-1">
        <div className="gradient-primary max-w-[85%] rounded-3xl px-4 py-2.5">
          <span className="sr-only">پیام شما:</span>
          <p className="text-[15px] leading-8 whitespace-pre-wrap break-words">{message.content}</p>
        </div>
        {meta}
      </div>
    )
  }

  // Nothing to show yet — the animated status pill under the transcript is the waiting UI.
  if (empty) return null

  // No card: the answer is the page, the way claude.ai reads. The user turn keeps its bubble,
  // and that asymmetry is what marks the two speakers apart.
  return (
    <article className="group flex w-full flex-col gap-2">
      <div ref={bodyRef} className="w-full text-foreground">
        <span className="sr-only">پاسخ دستیار:</span>

        {message.notice ? (
          <p className="mb-2 text-[12px] leading-6 text-muted-foreground">{message.notice}</p>
        ) : null}
        <Markdown content={message.content} sources={message.sources} onActiveSource={setActiveSource} />
        {message.pending ? <Caret /> : null}

        {message.sources ? (
          <SourcesList sources={message.sources} active={activeSource} onActive={setActiveSource} />
        ) : null}
      </div>
      {meta}
    </article>
  )
}
