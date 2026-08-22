'use client'

import * as React from 'react'

/** Tailwind's `lg`, the one breakpoint this panel changes shape at: above it the sidebar is a
 *  permanent column and the artifact pane sits beside the transcript; below it both of them
 *  cover the page, which makes them modals and obliges them to behave like ones. */
const DESKTOP = '(min-width: 64rem)'

/** Whether the viewport is at or above `lg`.
 *
 *  `useSyncExternalStore` rather than an effect: the value is read during render, so a
 *  component can pick the right element to render instead of rendering the wrong one and
 *  swapping it a frame later. The server snapshot is `true` on purpose — no window there, and
 *  assuming the column never opens a modal at a viewport that has no user in front of it. */
export function useIsDesktop(): boolean {
  return React.useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(DESKTOP)
      query.addEventListener('change', onChange)
      return () => query.removeEventListener('change', onChange)
    },
    () => window.matchMedia(DESKTOP).matches,
    () => true,
  )
}
