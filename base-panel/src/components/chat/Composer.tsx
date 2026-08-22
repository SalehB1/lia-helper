'use client'

import * as React from 'react'
import { Send2, Stop } from 'iconsax-reactjs'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** Mirrors MAX_MESSAGE_CHARS on the server so no user ever eats a 422. Duplicated on
 *  purpose, not generated — change both sides together. */
export const MAX_CHARS = 4000

/** The prompt box: a real form, Enter sends, Shift+Enter makes a newline. */
export function Composer({
  onSend,
  onStop,
  streaming,
}: {
  onSend: (text: string) => void
  onStop: () => void
  streaming: boolean
}) {
  const [value, setValue] = React.useState('')
  const ref = React.useRef<HTMLTextAreaElement>(null)

  // grow with the content, up to ~6 rows
  React.useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [value])

  function submit() {
    const text = value.trim()
    if (!text || streaming) return
    onSend(text.slice(0, MAX_CHARS))
    setValue('')
    ref.current?.focus()
  }

  return (
    <form
      // Same centred column as the transcript, so the two stay aligned once the scroller
      // around them spans the full width of the chat area.
      className="mx-auto w-full max-w-3xl shrink-0"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <label htmlFor="chat-input" className="sr-only">
        پرسش خود را بنویسید
      </label>
      <div className="glass flex items-end gap-2 rounded-3xl p-2 focus-within:ring-2 focus-within:ring-ring">
        <textarea
          id="chat-input"
          ref={ref}
          rows={1}
          value={value}
          maxLength={MAX_CHARS}
          placeholder="مثلاً: چطور اپلیکیشن Django را روی لیارا مستقر کنم؟"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              submit()
            }
          }}
          className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-1 py-2 text-sm leading-6 outline-none placeholder:text-muted-foreground"
        />
        {streaming ? (
          <Button type="button" variant="outline" size="default" onClick={onStop} className="shrink-0">
            <Stop className="size-4" variant="Bold" aria-hidden />
            توقف
          </Button>
        ) : (
          <Button type="submit" size="icon" disabled={!value.trim()} aria-label="ارسال" className="shrink-0">
            <Send2 className="size-4 rtl:-scale-x-100" aria-hidden />
          </Button>
        )}
      </div>
      <div className="mt-1.5 flex items-center gap-2 px-1 text-[11px] text-muted-foreground">
        <p className="flex-1">
          {streaming ? 'در حال نوشتن پاسخ…' : 'برای ارسال Enter و برای خط جدید Shift+Enter را بزنید.'}
        </p>
        {/* only once it starts to matter — a counter on an empty box is noise */}
        {value.length > MAX_CHARS * 0.8 ? (
          <span className={cn('tabular-nums', value.length >= MAX_CHARS && 'text-destructive')}>
            {value.length.toLocaleString('fa-IR')}/{MAX_CHARS.toLocaleString('fa-IR')}
          </span>
        ) : null}
      </div>
    </form>
  )
}
