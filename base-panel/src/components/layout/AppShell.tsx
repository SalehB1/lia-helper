'use client'

import { useEffect, useRef, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { Header } from './Header'
import { Sidebar } from './Sidebar'
import { useMe } from '@/components/auth/MeProvider'
import { useIsDesktop } from '@/hooks/useIsDesktop'

/** Pages anyone may reach. Everything else needs an account. */
const PUBLIC_PAGES = ['/login', '/register']

/**
 * Owns the single <main> and its padding, and the sign-in gate.
 *
 * The gate is client-side by necessity: the session cookie belongs to the API's origin, so
 * the Next server never receives it and no `proxy.ts` gate is possible. It is presentation —
 * the authoritative refusal is the per-endpoint dependency on the API, which 401s every route
 * but /healthz.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const drawerRef = useRef<HTMLDialogElement>(null)
  const isDesktop = useIsDesktop()
  const pathname = usePathname()
  const router = useRouter()
  const { me, loading } = useMe()
  const isPublic = PUBLIC_PAGES.includes(pathname)

  useEffect(() => {
    if (!loading && !me && !isPublic) router.replace('/login')
  }, [loading, me, isPublic, router])

  // `showModal` is the whole reason this is a <dialog>: Escape, the focus trap, the inert
  // background and the scroll lock all come from the platform instead of being reimplemented
  // — badly — over a div. `isDesktop` is in the condition, not a media query on the element:
  // a drawer left open while the viewport grows past `lg` would otherwise stay in the top
  // layer with `display: none`, holding the page inert with nothing on screen to close it.
  useEffect(() => {
    const drawer = drawerRef.current
    if (!drawer) return
    if (mobileOpen && !isDesktop) drawer.showModal()
    else if (drawer.open) drawer.close()
  }, [mobileOpen, isDesktop])

  // No rail and no header: the login screen is not a section of the panel, and `sectionFor`
  // would otherwise fall through and title it «گفت‌وگو».
  if (isPublic) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-4">
        <div className="w-full">{children}</div>
      </div>
    )
  }

  return (
    <div className="flex h-dvh gap-3 overflow-hidden p-3 lg:gap-4 lg:p-4">
      <aside className="hidden lg:block">
        <Sidebar />
      </aside>

      <dialog
        ref={drawerRef}
        aria-label="منوی اصلی"
        // Native close covers Escape and the form-method close; keeping React's state in
        // step here means every exit route funnels through one setter.
        onClose={() => setMobileOpen(false)}
        // A click on the ::backdrop is delivered with the dialog itself as the target, so
        // this is the backdrop test — the sidebar sits in the inner div and never matches.
        onClick={(event) => {
          if (event.target === drawerRef.current) setMobileOpen(false)
        }}
        // `w-fit`, not `w-auto`: a dialog whose inline insets both resolve to 0 stretches to
        // the viewport under `width: auto`, which ate the backdrop the drawer is closed by.
        className="fixed inset-y-0 start-0 m-0 h-dvh max-h-none w-fit max-w-full bg-transparent p-0 backdrop:bg-black/40 backdrop:backdrop-blur-sm"
      >
        <div className="h-full p-3">
          <Sidebar onNavigate={() => setMobileOpen(false)} />
        </div>
      </dialog>

      <div className="flex min-w-0 flex-1 flex-col gap-3 lg:gap-4">
        <Header onOpenSidebar={() => setMobileOpen(true)} />
        <main className="flex-1 overflow-y-auto p-1 lg:p-2">
          {me ? children : <p className="p-4 text-sm text-muted-foreground">در حال بررسی دسترسی…</p>}
        </main>
      </div>
    </div>
  )
}
