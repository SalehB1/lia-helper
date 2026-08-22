/** Shared class strings for form controls.
 *
 *  Not a component — the panel deliberately has no input component and hand-rolls its
 *  controls. But the same class string was pasted into four screens in three slightly
 *  different variants, and a settings form that focuses differently from the profile form is
 *  just a bug nobody filed. This is the one canonical spelling. */
export const FIELD =
  'h-10 w-full rounded-2xl border border-border bg-background px-3 text-sm outline-none ' +
  'transition focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed ' +
  'disabled:opacity-60'

export const FIELD_LABEL = 'text-sm font-medium'
