'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Add } from 'iconsax-reactjs'
import { cn } from '@/lib/utils'
import { ConversationList } from '@/components/chat/ConversationList'
import { useConversationsContext } from '@/components/chat/ConversationsProvider'
import { NAV_ITEMS, SUPERUSER_NAV_ITEMS, isSection, sectionFor } from './nav'
import { useMe } from '@/components/auth/MeProvider'

/** An icon rail of destinations, plus — only where a section actually owns something,
 *  which today means chat and its history — a second column for it. Repeating the rail
 *  as a list of links in that column is what it used to do; it said nothing new. */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname()
  const section = sectionFor(pathname)
  const { me } = useMe()
  const hasPanel = isSection(pathname, '/chat')

  return (
    <div className="flex h-full gap-3">
      <nav
        aria-label="ناوبری اصلی"
        className="glass flex h-full w-[4.5rem] shrink-0 flex-col items-center gap-1 overflow-y-auto rounded-3xl p-2 pt-5"
      >
        {/* same footprint and radius as a nav item, so the rail reads as one column of tiles */}
        <span
          className="gradient-primary mb-4 flex aspect-square w-full shrink-0 items-center justify-center rounded-lg text-lg font-bold"
          aria-hidden
        >
          ل
        </span>

        {/* The admin tiles appear only for a superuser — that is presentation, not
            protection: every one of these pages is refused server-side on its own. On the
            wire `role === 'admin'` means a REGULAR user; 'superuser' is the admin. */}
        {[...NAV_ITEMS, ...(me?.role === 'superuser' ? SUPERUSER_NAV_ITEMS : [])].map(({ href, short, label, icon: Icon }) => {
          // `sectionFor` is the single arbiter, and the Header titles the page by it — so
          // the lit tile and the title can never disagree. Asking `isSection` per item
          // cannot work here: /admin owns /admin/prompts too, so both tiles matched and
          // both lit up. `sectionFor` scans the operator list unconditionally while this
          // rail renders it only for a superuser; harmless, because a regular user cannot
          // reach /admin/* at all, but the two lists are no longer guaranteed to agree.
          const active = href === section.href
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              title={label}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'flex w-full flex-col items-center gap-1 rounded-lg px-1 py-2 text-[10px] transition-colors',
                active ? 'gradient-primary' : 'text-muted-foreground hover:bg-muted',
              )}
            >
              {/* filled while you are here, outlined everywhere else */}
              <Icon className="size-5 shrink-0" variant={active ? 'Bold' : 'Linear'} aria-hidden />
              <span className="w-full truncate text-center leading-4">{short}</span>
            </Link>
          )
        })}
      </nav>

      {hasPanel ? (
        // The rail (4.5rem) + this column + the drawer's own padding must leave a
        // tappable backdrop, or on a 320px phone the drawer covers the viewport and the
        // only way out is picking a destination you did not want.
        <div className="glass flex h-full w-60 max-w-[calc(100vw-9.5rem)] shrink-0 flex-col rounded-3xl p-3">
          <h2 className="px-2 pb-3 text-sm font-bold">{section.label}</h2>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <ChatSectionPanel onNavigate={onNavigate} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function ChatSectionPanel({ onNavigate }: { onNavigate?: () => void }) {
  const { items } = useConversationsContext()

  return (
    <div className="flex flex-col gap-2">
      <Link
        href="/chat"
        onClick={onNavigate}
        className="gradient-primary flex items-center justify-center gap-2 rounded-full px-3 py-2 text-sm font-medium transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Add className="size-4" aria-hidden />
        گفت‌وگوی جدید
      </Link>

      <p className="flex items-center gap-1 px-2 pt-1 text-[11px] text-muted-foreground">
        تاریخچه
        <span>({items.length.toLocaleString('fa-IR')})</span>
      </p>

      <ConversationList onNavigate={onNavigate} />
    </div>
  )
}
