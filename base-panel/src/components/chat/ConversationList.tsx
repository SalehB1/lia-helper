'use client'

import Link from 'next/link'
import { useRef, useState } from 'react'
import { Edit2, Trash } from 'iconsax-reactjs'
import { Button } from '@/components/ui/button'
import { FIELD } from '@/components/ui/field'
import { cn } from '@/lib/utils'
import { useConversationsContext } from './ConversationsProvider'

/** Mirrors the backend's MAX_TITLE_CHARS and the column width. Duplicated on purpose, per
 *  the client-limits rule — change both sides together. */
const MAX_TITLE_CHARS = 120

/** The row while it is being renamed. At module scope, never nested in the list, or every
 *  keystroke would remount the input and drop the caret. */
function TitleForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: string
  onSave: (title: string) => void
  onCancel: () => void
}) {
  return (
    <form
      className="min-w-0 flex-1"
      onSubmit={(event) => {
        event.preventDefault()
        const value = new FormData(event.currentTarget).get('title')
        onSave(typeof value === 'string' ? value : '')
      }}
    >
      <label htmlFor="conversation-title" className="sr-only">
        نام گفت‌وگو
      </label>
      <input
        id="conversation-title"
        name="title"
        defaultValue={initial}
        maxLength={MAX_TITLE_CHARS}
        autoFocus
        // `auto`, not the page direction: a title is «اتصال دیسک» as often as it is
        // «liara.json برای Django», and only the text itself knows which way it runs.
        dir="auto"
        onKeyDown={(event) => {
          if (event.key === 'Escape') onCancel()
        }}
        className={cn(FIELD, 'h-8 text-sm')}
      />
    </form>
  )
}

/** The history rows, in the sidebar's second column. Opening one navigates to the chat page. */
export function ConversationList({ onNavigate }: { onNavigate?: () => void }) {
  const { items, loading, error, activeUuid, remove, rename } = useConversationsContext()
  // One field, so one state — `useReducer` starts at five.
  const [editing, setEditing] = useState<string | null>(null)
  const pencils = useRef<Record<string, HTMLButtonElement | null>>({})

  // without this an unreachable API is indistinguishable from an empty history
  if (error) return <p className="px-2 py-1.5 text-xs leading-6 text-destructive">{error}</p>
  if (loading && items.length === 0) {
    return <p className="px-2 py-1.5 text-xs text-muted-foreground">در حال بارگذاری…</p>
  }
  if (items.length === 0) {
    return <p className="px-2 py-1.5 text-xs leading-6 text-muted-foreground">هنوز گفت‌وگویی ثبت نشده است.</p>
  }

  return (
    <ul className="flex flex-col gap-1">
      {items.map((item) => (
        <li key={item.uuid} className="flex items-center gap-1">
          {editing === item.uuid ? (
            <TitleForm
              initial={item.title}
              onSave={(title) => {
                setEditing(null)
                // Focus goes back to the control that opened the form, or Escape drops a
                // keyboard user on <body> with nowhere to tab from.
                pencils.current[item.uuid]?.focus()
                if (title.trim() && title.trim() !== item.title) void rename(item.uuid, title)
              }}
              onCancel={() => {
                setEditing(null)
                pencils.current[item.uuid]?.focus()
              }}
            />
          ) : (
            <>
              {/* A real href, not a click handler: the route IS the selection now, so this
                  row is a link a middle-click or a bookmark can follow like any other. */}
              <Link
                href={`/chat/${item.uuid}`}
                onClick={onNavigate}
                aria-current={item.uuid === activeUuid ? 'true' : undefined}
                className={cn(
                  'min-w-0 flex-1 truncate rounded-2xl px-2 py-1.5 text-start text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  item.uuid === activeUuid ? 'bg-muted font-medium' : 'hover:bg-muted',
                )}
              >
                {item.title}
              </Link>
              <Button
                ref={(node) => {
                  pencils.current[item.uuid] = node
                }}
                variant="ghost"
                size="icon"
                className="size-8 shrink-0 text-muted-foreground"
                aria-label={`تغییر نام گفت‌وگوی ${item.title}`}
                onClick={() => setEditing(item.uuid)}
              >
                <Edit2 className="size-4" aria-hidden />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 shrink-0 text-muted-foreground"
                aria-label={`حذف گفت‌وگوی ${item.title}`}
                onClick={() => void remove(item.uuid)}
              >
                <Trash className="size-4" aria-hidden />
              </Button>
            </>
          )}
        </li>
      ))}
    </ul>
  )
}
