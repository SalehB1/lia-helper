'use client'

import { useEffect, useState } from 'react'
import { Copy, TickCircle } from 'iconsax-reactjs'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** Writes `text` to the clipboard. Falls back to a hidden textarea + execCommand on
 *  insecure origins where navigator.clipboard is undefined. */
export async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}

export function CopyButton({
  text,
  label = 'کپی',
  className,
  iconOnly = false,
}: {
  text: string
  label?: string
  className?: string
  /** Icon-only, for dense rows like the message action bar. The label still reaches
   *  screen readers through aria-label. */
  iconOnly?: boolean
}) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  useEffect(() => {
    if (state === 'idle') return
    const timer = setTimeout(() => setState('idle'), 1800)
    return () => clearTimeout(timer)
  }, [state])

  const text_ = state === 'copied' ? 'کپی شد' : state === 'failed' ? 'کپی نشد' : label
  const Icon = state === 'copied' ? TickCircle : Copy

  return (
    <Button
      type="button"
      variant={iconOnly ? 'ghost' : 'outline'}
      size="sm"
      className={cn(iconOnly && 'size-8 px-0', className)}
      title={text_}
      aria-label={iconOnly ? text_ : undefined}
      onClick={async () => setState((await writeClipboard(text)) ? 'copied' : 'failed')}
    >
      <Icon className="size-4 shrink-0" variant={state === 'copied' ? 'Bold' : 'Linear'} aria-hidden />
      {iconOnly ? null : <span>{text_}</span>}
    </Button>
  )
}
