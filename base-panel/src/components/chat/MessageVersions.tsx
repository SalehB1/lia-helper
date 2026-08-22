'use client'

import { ArrowLeft2, ArrowRight2 } from 'iconsax-reactjs'
import { Button } from '@/components/ui/button'

/** Sibling-version navigation under a message, Claude-style: `‹ ۲ / ۳ ›`.
 *
 *  Versions are created by regenerating an answer or editing a question. The backend keeps
 *  them as siblings in the conversation tree and each one owns the branch below it, so
 *  moving between them replaces everything that followed — which is why this asks the
 *  server to switch rather than swapping text locally.
 *
 *  The arrows are the LOGICAL previous/next, not screen directions: in this RTL layout the
 *  start-pointing glyph is the one that walks backwards through the versions. */
export function MessageVersions({
  index,
  count,
  onPrev,
  onNext,
  disabled,
}: {
  index: number
  count: number
  onPrev: () => void
  onNext: () => void
  disabled?: boolean
}) {
  if (count < 2) return null

  return (
    <div className="flex items-center gap-0.5 text-[11px] text-muted-foreground">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="size-6 px-0"
        title="نسخهٔ قبلی"
        aria-label="نمایش نسخهٔ قبلی"
        disabled={disabled || index <= 1}
        onClick={onPrev}
      >
        <ArrowRight2 className="size-3.5 shrink-0" aria-hidden />
      </Button>
      <span aria-live="off">
        {index.toLocaleString('fa-IR')} / {count.toLocaleString('fa-IR')}
      </span>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="size-6 px-0"
        title="نسخهٔ بعدی"
        aria-label="نمایش نسخهٔ بعدی"
        disabled={disabled || index >= count}
        onClick={onNext}
      >
        <ArrowLeft2 className="size-3.5 shrink-0" aria-hidden />
      </Button>
    </div>
  )
}
