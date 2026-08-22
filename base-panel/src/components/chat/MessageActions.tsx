'use client'

import { Edit2, Refresh2 } from 'iconsax-reactjs'
import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/CopyButton'
import { cn } from '@/lib/utils'

/** Per-message controls. Hidden until hover/focus on pointer devices; always visible on
 *  touch, where there is no hover to reveal them. */
export function MessageActions({
  text,
  onRetry,
  retryLabel = 'تولید دوبارهٔ پاسخ',
  onEdit,
  className,
}: {
  text: string
  onRetry?: () => void
  /** What retrying means here: regenerating an answer, or answering a question that never
   *  got one. Same control, and the difference is invisible without saying it. */
  retryLabel?: string
  onEdit?: () => void
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-0.5 text-muted-foreground transition-opacity',
        'md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100',
        className,
      )}
    >
      <CopyButton text={text} iconOnly />
      {onEdit ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="size-8 px-0"
          title="ویرایش"
          aria-label="ویرایش پیام"
          onClick={onEdit}
        >
          <Edit2 className="size-4 shrink-0" aria-hidden />
        </Button>
      ) : null}
      {onRetry ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="size-8 px-0"
          title={retryLabel}
          aria-label={retryLabel}
          onClick={onRetry}
        >
          <Refresh2 className="size-4 shrink-0" aria-hidden />
        </Button>
      ) : null}
    </div>
  )
}
