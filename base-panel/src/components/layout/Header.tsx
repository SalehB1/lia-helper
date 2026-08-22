'use client'

import { usePathname } from 'next/navigation'
import { useTheme } from 'next-themes'
import { HamburgerMenu, Moon, Sun1 } from 'iconsax-reactjs'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { sectionFor } from './nav'
import { useMe } from '@/components/auth/MeProvider'

/** A 44px bar that earns its height: which section you are in, plus that section's
 *  controls. Taller on touch, where 44px of bar leaves no room for a 44px target. */
export function Header({ onOpenSidebar }: { onOpenSidebar: () => void }) {
  const pathname = usePathname()
  const section = sectionFor(pathname)
  const { me, signOut } = useMe()
  const [signingOut, setSigningOut] = useState(false)
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  return (
    <header className="glass flex h-12 shrink-0 items-center gap-1 rounded-3xl px-2 lg:h-11 lg:px-3">
      <Button
        variant="ghost"
        size="icon"
        className="size-11 lg:hidden"
        onClick={onOpenSidebar}
        aria-label="باز کردن منو"
      >
        <HamburgerMenu className="size-5" aria-hidden />
      </Button>

      <h1 className="truncate px-1.5 text-sm font-semibold">{section.label}</h1>

      <div className="flex-1" />

      {me ? (
        <>
          <span className="hidden truncate px-1 text-xs text-muted-foreground sm:inline">
            {me.displayName || me.username}
          </span>
          <Button
            variant="ghost"
            size="sm"
            disabled={signingOut}
            onClick={async () => {
              setSigningOut(true)
              await signOut()
              // A full assign, not a router push: the signed-out document should go away
              // with everything it was still holding.
              window.location.assign('/login')
            }}
          >
            {signingOut ? 'در حال خروج…' : 'خروج'}
          </Button>
        </>
      ) : null}

      <Button
        variant="ghost"
        size="icon"
        className="size-11 lg:size-9"
        aria-label="تغییر پوسته"
        onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
      >
        {/* render nothing theme-dependent until mounted — server HTML has no theme */}
        {mounted && resolvedTheme === 'dark' ? <Sun1 className="size-5" aria-hidden /> : <Moon className="size-5" aria-hidden />}
      </Button>
    </header>
  )
}
