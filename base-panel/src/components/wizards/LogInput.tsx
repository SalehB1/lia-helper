'use client'

import { cn } from '@/lib/utils'

/** Mirrors MAX_LOG_CHARS / DiagnoseRequest in the backend contract. */
export const MAX_LOG_CHARS = 6000
export const MIN_LOG_CHARS = 5

const FA_NUMBER = new Intl.NumberFormat('fa-IR')

export function LogInput({
  value,
  onChange,
  disabled = false,
  id = 'diagnose-log',
}: {
  value: string
  onChange: (next: string) => void
  disabled?: boolean
  id?: string
}) {
  const counterId = `${id}-counter`
  const over = value.length > MAX_LOG_CHARS

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        لاگ خطا
      </label>
      <p className="text-muted-foreground text-xs">
        خروجی مرحلهٔ بیلد یا لاگ اجرای برنامه را این‌جا بچسبانید. متن همان‌طور که هست فرستاده می‌شود؛
        کلیدها و رمزها را پیش از ارسال پاک کنید.
      </p>
      <textarea
        id={id}
        dir="ltr"
        rows={12}
        spellCheck={false}
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={counterId}
        aria-invalid={over || undefined}
        placeholder="npm ERR! code ELIFECYCLE …"
        className={cn(
          'w-full resize-y rounded-2xl border bg-background p-3 text-start font-mono text-xs leading-relaxed',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          'disabled:opacity-50',
          over ? 'border-destructive' : 'border-border',
        )}
      />
      <p
        id={counterId}
        aria-live="polite"
        className={cn('text-xs', over ? 'text-destructive font-medium' : 'text-muted-foreground')}
      >
        {FA_NUMBER.format(value.length)} از {FA_NUMBER.format(MAX_LOG_CHARS)} نویسه
        {over ? ' — بیش از حد مجاز؛ متن را کوتاه کنید.' : ''}
      </p>
    </div>
  )
}
