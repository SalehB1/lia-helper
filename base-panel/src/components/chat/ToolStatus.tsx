'use client'

/** Live label of what the agent is doing right now — shown only while it works. No chip, no
 *  spinner: plain shimmering text at the inline-end of the answer it belongs to. The negative
 *  top margin eats most of the turn gap so it reads as attached to that answer, not floating.
 *  `aria-hidden` because MessageList already announces the same text through its live region. */
export function ToolStatus({ label }: { label: string }) {
  return (
    <p className="-mt-7 text-end text-xs" aria-hidden>
      <span className="shimmer-text">{label}…</span>
    </p>
  )
}
