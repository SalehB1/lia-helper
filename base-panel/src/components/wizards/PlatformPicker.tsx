'use client'

import { TickCircle } from 'iconsax-reactjs'
import { cn } from '@/lib/utils'

/** Mirrors the backend `Platform` enum (app/shared/enums.py) exactly. */
export type PlatformId =
  | 'nodejs'
  | 'python'
  | 'django'
  | 'flask'
  | 'fastapi'
  | 'laravel'
  | 'php'
  | 'nextjs'
  | 'react'
  | 'vue'
  | 'angular'
  | 'static'
  | 'docker'
  | 'go'
  | 'dotnet'

export type PlatformOption = { id: PlatformId; name: string; fa: string }

export const PLATFORMS: PlatformOption[] = [
  { id: 'nodejs', name: 'Node.js', fa: 'نود جی‌اس' },
  { id: 'python', name: 'Python', fa: 'پایتون' },
  { id: 'django', name: 'Django', fa: 'جنگو' },
  { id: 'flask', name: 'Flask', fa: 'فلسک' },
  { id: 'fastapi', name: 'FastAPI', fa: 'فست‌ای‌پی‌آی' },
  { id: 'laravel', name: 'Laravel', fa: 'لاراول' },
  { id: 'php', name: 'PHP', fa: 'پی‌اچ‌پی' },
  { id: 'nextjs', name: 'Next.js', fa: 'نکست جی‌اس' },
  { id: 'react', name: 'React', fa: 'ری‌اکت' },
  { id: 'vue', name: 'Vue', fa: 'ویو' },
  { id: 'angular', name: 'Angular', fa: 'انگولار' },
  { id: 'static', name: 'Static', fa: 'میزبانی استاتیک' },
  { id: 'docker', name: 'Docker', fa: 'داکر' },
  { id: 'go', name: 'Go', fa: 'گو' },
  { id: 'dotnet', name: '.NET', fa: 'دات‌نت' },
]

export function platformLabel(id: PlatformId | null): string {
  const found = PLATFORMS.find((platform) => platform.id === id)
  return found ? `${found.name} — ${found.fa}` : 'انتخاب‌نشده'
}

/** Keyboard-operable card grid. Plain buttons with aria-pressed (not a radiogroup, so
 *  Tab/Enter/Space behave exactly as a screen reader user expects without roving focus). */
export function PlatformPicker({
  value,
  onChange,
  disabled = false,
  labelledBy,
}: {
  value: PlatformId | null
  onChange: (id: PlatformId) => void
  disabled?: boolean
  labelledBy?: string
}) {
  return (
    <div
      role="group"
      aria-labelledby={labelledBy}
      className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
    >
      {PLATFORMS.map((platform) => {
        const selected = platform.id === value
        return (
          <button
            key={platform.id}
            type="button"
            disabled={disabled}
            aria-pressed={selected}
            onClick={() => onChange(platform.id)}
            className={cn(
              'flex min-h-16 flex-col items-start justify-center gap-1 rounded-lg border p-3 text-start transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
              'disabled:pointer-events-none disabled:opacity-50',
              selected ? 'border-primary bg-muted' : 'glass glass-interactive',
            )}
          >
            <span className="flex w-full items-center justify-between gap-2">
              <span dir="ltr" className="font-mono text-sm font-semibold">
                {platform.name}
              </span>
              {selected && <TickCircle className="size-4 shrink-0 text-primary" aria-hidden />}
            </span>
            <span className="text-muted-foreground text-xs">{platform.fa}</span>
          </button>
        )
      })}
    </div>
  )
}
