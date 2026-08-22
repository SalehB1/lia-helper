'use client'

import * as React from 'react'
import { ArrowDown2, Refresh2, Warning2 } from 'iconsax-reactjs'
import { Button } from '@/components/ui/button'
import type { ChatMessage } from '@/hooks/useChat'
import { EmptyState } from './EmptyState'
import { MessageBubble } from './MessageBubble'
import { SuggestionChips } from './SuggestionChips'
import { ToolStatus } from './ToolStatus'

/** How far from the bottom still counts as "following the stream". */
const STICK_PX = 160

/** `۲۰ مرداد` — the divider between two days of the same conversation. */
function dayLabel(iso?: string): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('fa-IR', { day: 'numeric', month: 'long' })
}

function Skeleton() {
  return (
    <div className="flex flex-col gap-4" aria-hidden>
      <div className="ms-auto h-12 w-2/5 animate-pulse rounded-3xl bg-muted" />
      <div className="h-28 w-full animate-pulse rounded-3xl bg-muted" />
      <div className="ms-auto h-12 w-1/3 animate-pulse rounded-3xl bg-muted" />
    </div>
  )
}

/** The scrolling transcript. Owns auto-scroll and every non-empty chat state. */
export function MessageList({
  messages,
  streaming,
  loading,
  toolStatus,
  error,
  suggestions,
  onSend,
  onRetry,
  onEdit,
  onSwitchVersion,
}: {
  messages: ChatMessage[]
  streaming: boolean
  loading: boolean
  toolStatus: string | null
  error: string | null
  suggestions: string[]
  onSend: (text: string) => void
  onRetry: () => void
  onEdit: (uuid: string, content: string) => void
  onSwitchVersion: (uuid: string, versionIndex: number) => void
}) {
  const ref = React.useRef<HTMLDivElement>(null)
  const [stuck, setStuck] = React.useState(true)
  // Whether there is anywhere to scroll at all. Derived from the element rather than the
  // message count: an unscrollable transcript never fires a scroll event, so a `stuck`
  // left false by a longer conversation would strand the jump button with no way to
  // dismiss it.
  const [scrollable, setScrollable] = React.useState(false)
  const last = messages[messages.length - 1]

  // What the assistant is doing right now: the tool the backend reported, or plain
  // thinking for the two silent gaps — before the first tool starts, and between a tool
  // ending and the first answer token.
  const status = streaming
    ? (toolStatus ?? (last?.pending && !last.content ? 'در حال فکر کردن' : null))
    : null

  // A screen reader gets ONE announcement, when the answer is finished. Marking the whole
  // transcript live would re-announce every past message on every streamed token.
  const [announcement, setAnnouncement] = React.useState('')
  React.useEffect(() => {
    if (!streaming && last?.role === 'assistant' && !last.pending && last.content) {
      setAnnouncement(last.content)
    }
  }, [streaming, last])

  // follow the stream, but never yank the view away from someone reading above
  React.useEffect(() => {
    const el = ref.current
    if (!el) return
    if (stuck) el.scrollTop = el.scrollHeight
    measure(el)
  }, [messages, status, suggestions, error, stuck])

  function measure(el: HTMLDivElement) {
    const overflow = el.scrollHeight - el.clientHeight
    setScrollable(overflow > STICK_PX)
    // Recomputed here as well as on scroll: switching to a shorter conversation changes
    // the distance to the bottom without the user scrolling, and only a scroll event
    // would otherwise correct it.
    if (overflow <= STICK_PX) setStuck(true)
  }

  function onScroll(event: React.UIEvent<HTMLDivElement>) {
    const el = event.currentTarget
    setStuck(el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX)
    measure(el)
  }

  function toBottom() {
    const el = ref.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    setStuck(true)
  }

  return (
    <div className="relative min-h-0 flex-1">
      {/* `relative` so the sr-only spans inside a message are contained — and therefore clipped —
          by the scroller instead of by the box above it, which gave <main> a second scrollbar. */}
      {/* The scroller spans the whole chat area and the reading column is centred INSIDE it,
          so the RTL scrollbar sits at the far edge of the area rather than slicing down the
          middle of the screen beside the text. */}
      {/* `scrollbar-gutter: stable both-edges` — the scrollbar reserves its track on ONE
          side, which would slide the centred column half a bar away from the composer
          below it (measured: 6px). Reserving both edges keeps the two aligned, and keeps
          them aligned whether or not the transcript is long enough to scroll. */}
      <div
        ref={ref}
        onScroll={onScroll}
        className="relative h-full overflow-y-auto [scrollbar-gutter:stable_both-edges]"
        tabIndex={-1}
      >
        {/* gap-10 (40px), not gap-6: a paragraph break inside an answer is 16px, so at 24px the
            boundary between two turns barely outweighed the one between two paragraphs. The
            bottom padding is what lets the last line scroll clear of the jump-to-bottom pill. */}
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-10 pb-12" aria-busy={streaming}>
          <div className="sr-only" aria-live="polite">
            {announcement}
          </div>
          {/* Mounted for the whole session on purpose: a live region inserted together
              with its own text is not announced, so the pill needs a region that was
              already in the tree when the status appears. */}
          <div className="sr-only" aria-live="polite">
            {status}
          </div>

          {loading ? <Skeleton /> : null}

          {!loading && messages.length === 0 && !error ? <EmptyState onPick={onSend} /> : null}

          {messages.map((message, i) => {
            const day = dayLabel(message.createdAt)
            const newDay = day && day !== dayLabel(messages[i - 1]?.createdAt)
            // Either role, so long as it ends the branch. An answer can be regenerated; a
            // question with nothing under it is a turn that died, and this is its only
            // recovery once the transient error box is gone — after a reload, say.
            const isLast = i === messages.length - 1
            return (
              <React.Fragment key={message.uuid}>
                {newDay ? (
                  <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                    <span className="h-px flex-1 bg-border" />
                    <span>{day}</span>
                    <span className="h-px flex-1 bg-border" />
                  </div>
                ) : null}
                <MessageBubble
                  message={message}
                  onRetry={isLast && !streaming ? onRetry : undefined}
                  onEdit={streaming ? undefined : onEdit}
                  onSwitchVersion={onSwitchVersion}
                  busy={streaming}
                />
              </React.Fragment>
            )
          })}

          {status ? <ToolStatus label={status} /> : null}

          {error ? (
            <div
              aria-live="polite"
              className="glass flex flex-wrap items-center gap-3 rounded-3xl border-destructive p-3 text-sm text-card-foreground"
            >
              <Warning2 className="size-4 shrink-0 text-destructive" aria-hidden />
              <p className="min-w-0 flex-1">{error}</p>
              <Button variant="outline" size="sm" onClick={onRetry}>
                <Refresh2 className="size-3.5" aria-hidden />
                تلاش دوباره
              </Button>
            </div>
          ) : null}

          {!streaming && !error && suggestions.length > 0 && last?.role === 'assistant' ? (
            <SuggestionChips items={suggestions} onSelect={onSend} disabled={streaming} />
          ) : null}
        </div>
      </div>

      {/* An icon-only circle at the inline-end edge, not a labelled pill across the column:
          it floats over the transcript, so its footprint is what it costs the reader. The
          old full-width pill covered half a line of the answer mid-sentence. `glass-solid`
          because a 55% pane over scrolling text leaves both unreadable. */}
      {scrollable && !stuck ? (
        // Pinned to the reading column, not to the chat area: now that the scroller is
        // full-width, `end-2` on its own would park the pill on top of the scrollbar.
        <div className="pointer-events-none absolute inset-x-0 bottom-2 mx-auto flex w-full max-w-3xl justify-end">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={toBottom}
            title="رفتن به آخرین پیام"
            aria-label="رفتن به آخرین پیام"
            className="glass glass-solid pointer-events-auto me-2 size-9 rounded-full px-0"
          >
            <ArrowDown2 className="size-4 shrink-0" aria-hidden />
          </Button>
        </div>
      ) : null}
    </div>
  )
}
