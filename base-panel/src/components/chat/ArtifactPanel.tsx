'use client'

import * as React from 'react'
import { CloseCircle } from 'iconsax-reactjs'
import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/CopyButton'
import { useIsDesktop } from '@/hooks/useIsDesktop'
import { cn } from '@/lib/utils'
import { useArtifact } from './ArtifactProvider'
import { artifactName, HighlightedCode } from './Markdown'

/** The code a message sent aside. A sibling column of the transcript, so in RTL it lands on the
 *  inline-end edge on its own; below `lg` it covers the conversation instead of squeezing it.
 *
 *  It is a real `<dialog>`: `showModal()` is what puts the transcript, the sidebar and the header
 *  in the inert top-layer shadow, so a keyboard or screen-reader user cannot walk into content
 *  hidden under an opaque overlay. At `lg` it opens non-modally and is labelled `complementary`,
 *  because there it really is just a column. */
export function ArtifactPanel() {
  const artifact = useArtifact()
  const isDesktop = useIsDesktop()
  const dialogRef = React.useRef<HTMLDialogElement>(null)
  const closeRef = React.useRef<HTMLButtonElement>(null)
  const returnTo = React.useRef<HTMLElement | null>(null)
  const code = artifact?.code ?? null
  const close = artifact?.close
  const open = code !== null && close !== undefined

  React.useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog || !open) return
    returnTo.current = document.activeElement as HTMLElement | null
    if (isDesktop) dialog.show()
    else dialog.showModal()
    closeRef.current?.focus()

    // Escape closes a modal dialog natively but not a non-modal one, and either way React has to
    // hear about it — so the listener owns Escape in both modes.
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') close?.()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      // Only take focus back if it is still ours to give — otherwise the user has already
      // clicked or tabbed somewhere else and being yanked to the composer is worse.
      const active = document.activeElement
      const ours = !active || active === document.body || dialog.contains(active)
      if (dialog.open) dialog.close()
      if (!ours) return
      // Back to the card that opened it — or, when that card is gone (the conversation changed
      // under the panel), to the composer, so the next Tab does not restart at the top of the page.
      const back = returnTo.current?.isConnected
        ? returnTo.current
        : document.getElementById('chat-input')
      back?.focus({ preventScroll: true })
    }
  }, [open, isDesktop, close])

  if (!open || !close) return null

  const name = artifactName(code, artifact.lang)
  const lines = code.split('\n').length

  return (
    <dialog
      ref={dialogRef}
      role={isDesktop ? 'complementary' : undefined}
      aria-modal={isDesktop ? undefined : true}
      aria-labelledby="artifact-title"
      className={cn(
        // The pane floats over the transcript below `lg`, so it needs an opaque floor: the
        // ::backdrop, which only exists while it is modal.
        'glass glass-solid m-0 flex max-h-none max-w-none flex-col overflow-hidden p-0 backdrop:bg-background',
        isDesktop
          ? 'relative inset-auto h-full w-[32rem] max-w-[45%] shrink-0 rounded-3xl'
          : 'fixed inset-0 h-dvh w-dvw',
      )}
    >
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 id="artifact-title" className="truncate text-sm font-semibold text-card-foreground">
            <bdi dir="ltr">{name}</bdi>
          </h2>
          <p className="truncate text-[11px] text-muted-foreground">
            {artifact.lang ? (
              <>
                <bdi dir="ltr">{artifact.lang}</bdi>
                {' · '}
              </>
            ) : null}
            {lines.toLocaleString('fa-IR')} خط
          </p>
        </div>
        <CopyButton text={code} />
        <Button
          ref={closeRef}
          type="button"
          variant="ghost"
          size="sm"
          className="size-8 px-0"
          onClick={close}
          aria-label="بستن کد"
          title="بستن کد"
        >
          <CloseCircle className="size-4" aria-hidden />
        </Button>
      </header>

      {/* Code stays LTR and scrolls on both axes inside its own box. */}
      <div dir="ltr" className="min-h-0 flex-1 overflow-auto p-3">
        <HighlightedCode code={code} lang={artifact.lang} />
      </div>
    </dialog>
  )
}
