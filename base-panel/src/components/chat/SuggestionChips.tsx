'use client'

import { ArrowLeft } from 'iconsax-reactjs'

/** Model-proposed follow-ups. Clicking one sends it as the next question. */
export function SuggestionChips({
  items,
  onSelect,
  disabled,
}: {
  items: string[]
  onSelect: (text: string) => void
  disabled?: boolean
}) {
  if (!items.length) return null

  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="پیشنهاد ادامهٔ گفت‌وگو">
      {items.map((item) => (
        <button
          key={item}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(item)}
          className="inline-flex items-center gap-1.5 glass glass-interactive rounded-full px-3 py-1.5 text-xs text-card-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
        >
          <ArrowLeft className="size-3 shrink-0 text-muted-foreground" aria-hidden />
          <span className="text-start">{item}</span>
        </button>
      ))}
    </div>
  )
}
