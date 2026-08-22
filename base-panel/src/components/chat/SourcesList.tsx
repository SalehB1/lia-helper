'use client'

import { ExportSquare } from 'iconsax-reactjs'
import { cn } from '@/lib/utils'
import { safeHref, type SourceRef } from '@/lib/api'

/** `docs.liara.ir/paas` → `docs.liara.ir`. Falsy URLs just render nothing. */
function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** Numbered citations under an assistant answer, as one-line chips. `[n]` in the text points here,
 *  and hovering either side lights up the other. Heading + domain live in the chip's `title`. */
export function SourcesList({
  sources,
  active,
  onActive,
}: {
  sources: SourceRef[]
  active?: number | null
  onActive?: (n: number | null) => void
}) {
  if (!sources.length) return null

  return (
    <section className="mt-5 border-t border-border pt-3">
      <h4 className="mb-1.5 text-[11px] font-medium text-muted-foreground">
        منابع ({sources.length.toLocaleString('fa-IR')})
      </h4>
      <ul className="flex flex-wrap gap-1.5">
        {sources.map((source) => (
          <li key={`${source.n}-${source.url}`}>
            <a
              href={safeHref(source.url)}
              target="_blank"
              rel="noopener noreferrer"
              title={[source.title, source.heading, domainOf(source.url)].filter(Boolean).join(' — ')}
              onMouseEnter={() => onActive?.(source.n)}
              onMouseLeave={() => onActive?.(null)}
              onFocus={() => onActive?.(source.n)}
              onBlur={() => onActive?.(null)}
              className={cn(
                'glass glass-interactive inline-flex cursor-pointer items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                active === source.n && 'ring-2 ring-primary',
              )}
            >
              <span className="inline-flex size-4 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold text-muted-foreground">
                {source.n.toLocaleString('fa-IR')}
              </span>
              <span className="line-clamp-1 max-w-[18ch] font-medium text-card-foreground sm:max-w-[28ch]">
                {source.title}
              </span>
              <ExportSquare className="size-3 shrink-0 text-muted-foreground" aria-hidden />
            </a>
          </li>
        ))}
      </ul>
    </section>
  )
}
