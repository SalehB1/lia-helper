'use client'

import * as React from 'react'
import { Button } from '@/components/ui/button'
import { MAX_CHARS } from './Composer'

/** Inline rewrite of one of your own questions.
 *
 *  Submitting does not overwrite the original: the backend keeps it as a sibling version,
 *  so both stay reachable through the version arrows. Everything that followed the original
 *  belongs to the other branch and leaves the transcript. */
export function MessageEditor({
  initial,
  onSubmit,
  onCancel,
}: {
  initial: string
  onSubmit: (text: string) => void
  onCancel: () => void
}) {
  const [text, setText] = React.useState(initial)
  const ref = React.useRef<HTMLTextAreaElement>(null)

  React.useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [])

  const trimmed = text.trim()
  const unchanged = trimmed === initial.trim()

  function submit() {
    if (!trimmed || unchanged) return
    onSubmit(trimmed)
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      onCancel()
    }
    // Enter sends, Shift+Enter breaks the line — the same contract as the composer.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className="glass flex w-full flex-col gap-2 rounded-3xl p-3">
      <label htmlFor="message-editor" className="sr-only">
        ویرایش پیام شما
      </label>
      <textarea
        id="message-editor"
        ref={ref}
        rows={3}
        value={text}
        maxLength={MAX_CHARS}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        className="w-full resize-none bg-transparent text-[15px] leading-8 text-foreground outline-none"
      />
      <div className="flex items-center justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          انصراف
        </Button>
        <Button type="button" size="sm" disabled={!trimmed || unchanged} onClick={submit}>
          ارسال
        </Button>
      </div>
    </div>
  )
}
