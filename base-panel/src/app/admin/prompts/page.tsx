'use client'

import { useCallback, useEffect, useState } from 'react'
import { ArrowRotateLeft, Save2 } from 'iconsax-reactjs'
import { PageContainer } from '@/components/layout/PageContainer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { FIELD_LABEL } from '@/components/ui/field'
import { useMe } from '@/components/auth/MeProvider'
import { ApiError, apiFetch } from '@/lib/api'
import { cn } from '@/lib/utils'

/** One prompt, exactly as the backend describes it. `key` is already camelCase, so what is
 *  read here is what gets sent back. */
type Prompt = {
  key: string
  text: string
  default: string
  source: 'default' | 'db'
  maxChars: number
  requiredFragments: string[]
  forbiddenFragments: string[]
}

type Rejection = { key: string; reasons: string[] }

const FA_NUMBER = new Intl.NumberFormat('fa-IR')

/** A sentence about what each text does, not a name for a constant. */
const COPY: Record<string, { label: string; help: string }> = {
  system: {
    label: 'دستورالعمل اصلی گفت‌وگو',
    help: 'در هر گفت‌وگو به مدل داده می‌شود و همهٔ قانون‌های پاسخ دادن در همین متن است: کجا جست‌وجو کند، چطور منبع بدهد و کجا بگوید پاسخ را پیدا نکردم.',
  },
  finalRound: {
    label: 'دستورالعمل نوشتن پاسخ نهایی',
    help: 'درست پیش از نوشتن پاسخ نهایی داده می‌شود، وقتی دیگر ابزاری در اختیار مدل نیست.',
  },
  wizard: {
    label: 'دستورالعمل ویزاردها',
    help: 'برای تولید پیکربندی و عیب‌یابی لاگ، که یک‌مرحله‌ای‌اند و خروجی را همان‌جا کامل می‌دهند.',
  },
  handoff: {
    label: 'متن دعوت به پشتیبانی',
    help: 'همان چیزی که به کاربرِ به‌نتیجه‌نرسیده گفته می‌شود؛ بنویسید از کجا می‌تواند با یک آدم حرف بزند — تیکت، تلفن، هرچه دارید. با زبان خود دستیار بنویسید، نه به شکل دستور به مدل: همین متن ممکن است عیناً به کاربر نشان داده شود. تا وقتی این سیاست را در «تنظیمات دستیار» روشن نکنید، جایی استفاده نمی‌شود.',
  },
  handoffPhrases: {
    label: 'عبارت‌هایی که یعنی کاربر به نتیجه نرسیده',
    help: 'هر خط یک عبارت. اگر پیام کاربر یکی از این‌ها را داشته باشد، آن پیام «ناراضی» شمرده می‌شود. عبارت‌های خیلی کوتاه نادیده گرفته می‌شوند، چون داخل کلمه‌های بی‌ربط هم پیدا می‌شوند. خالی گذاشتن این کادر یعنی سیاست هیچ‌وقت اجرا نشود.',
  },
}

/** Mirrors `prompt_settings.MAX_PROMPT_CHARS`; the server sends its own value with every
 *  record and that one wins. This is only the fallback before the first response arrives. */
const MAX_CHARS_FALLBACK = 20000

/** Turn one machine-readable reason code into the sentence an operator can act on. For a
 *  prompt the reason IS the message: "which rule did I delete" is the only useful answer. */
function reasonText(reason: string): string {
  const separator = reason.indexOf(':')
  const code = separator === -1 ? reason : reason.slice(0, separator)
  const what = separator === -1 ? '' : reason.slice(separator + 1)
  if (code === 'missing_fragment') return `عبارت «${what}» باید در متن بماند؛ حذف شده است.`
  if (code === 'forbidden_fragment') return `عبارت «${what}» نباید در این متن بیاید.`
  if (code === 'tool_not_named')
    return `نام ابزار ${what} باید در متن بیاید، وگرنه مدل هیچ‌وقت سراغش نمی‌رود.`
  if (code === 'too_long') return 'متن از سقف مجاز بلندتر است.'
  return reason
}

/**
 * The texts that decide how the assistant answers.
 *
 * Superuser only — refused server-side on the endpoint itself; the card below is only what
 * this page shows a regular operator instead of a raw 403. A saved text applies to the next
 * answer with no restart. Every rule is re-checked server-side, so a text that drops a
 * load-bearing rule is refused there whatever this page sends.
 */
export default function AdminPromptsPage() {
  const { me, loading: loadingMe } = useMe()
  const [prompts, setPrompts] = useState<Prompt[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [previous, setPrevious] = useState<Record<string, string>>({})
  const [rejected, setRejected] = useState<Rejection[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  /**
   * `keep` names the keys the server refused. Their draft is the operator's unsaved work —
   * re-seeding it from the in-force text discards several minutes of rewriting, and undo
   * cannot recover it because `previous` holds that same in-force text.
   */
  const apply = useCallback((list: Prompt[], keep: ReadonlySet<string> = new Set()) => {
    setPrompts(list)
    setDrafts((current) =>
      Object.fromEntries(
        list.map((prompt) => [
          prompt.key,
          keep.has(prompt.key) ? (current[prompt.key] ?? prompt.text) : prompt.text,
        ]),
      ),
    )
  }, [])

  useEffect(() => {
    if (loadingMe || me?.role !== 'superuser') return
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{ prompts: Prompt[] }>('/api/v1/admin/prompts')
        if (alive) apply(data.prompts)
      } catch (err) {
        if (alive) setError(err instanceof ApiError ? err.message : 'متن‌ها بارگذاری نشد.')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [apply, me, loadingMe])

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    setSaved(false)
    setRejected([])
    try {
      const payload: Record<string, string | null> = {}
      for (const prompt of prompts) {
        const draft = drafts[prompt.key] ?? ''
        if (draft === prompt.text) continue
        // Back to the shipped text means DELETE the row, not store a copy of it — otherwise
        // a prompt improved in a later release would never reach this deployment again.
        payload[prompt.key] = draft.trim() === prompt.default.trim() ? null : draft
      }
      const before = Object.fromEntries(prompts.map((prompt) => [prompt.key, prompt.text]))
      const data = await apiFetch<{ prompts: Prompt[]; rejected?: Rejection[] }>(
        '/api/v1/admin/prompts',
        { method: 'PUT', body: JSON.stringify(payload) },
      )
      setPrevious(before)
      apply(data.prompts, new Set((data.rejected ?? []).map((item) => item.key)))
      if (data.rejected?.length) {
        // The server refuses a text it will not accept and still answers 200. Saying which
        // rule went missing is the difference between "live" and "silently ignored".
        setRejected(data.rejected)
      } else {
        setSaved(true)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'ذخیره نشد.')
    } finally {
      setSaving(false)
    }
  }

  if (loadingMe || (me?.role === 'superuser' && loading)) {
    return (
      <PageContainer size="md">
        <p className="text-sm text-muted-foreground">در حال بارگذاری…</p>
      </PageContainer>
    )
  }

  if (me?.role !== 'superuser') {
    return (
      <PageContainer size="md">
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            فقط مدیر ارشد اجازهٔ دیدن این صفحه را دارد.
          </CardContent>
        </Card>
      </PageContainer>
    )
  }

  return (
    <PageContainer size="md">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">متن دستورالعمل دستیار</h1>
        <p className="text-sm text-muted-foreground">
          این متن‌ها در پایگاه‌داده ذخیره می‌شوند و از همان پاسخ بعدی اثر می‌گذارند. چند عبارت
          پایه‌ای باید در متن بمانند؛ اگر حذف شوند، متن ذخیره نمی‌شود و همان چیزی که در حال کار
          است سر جایش می‌ماند.
        </p>
      </header>

      {error ? (
        <Card className="border-destructive">
          <CardContent className="py-4 text-sm text-destructive" aria-live="polite">
            {error}
          </CardContent>
        </Card>
      ) : null}

      {prompts.length > 0 ? (
        <form onSubmit={save} className="flex flex-col gap-6">
          {prompts.map((prompt) => {
            const id = `prompt-${prompt.key}`
            const counterId = `${id}-counter`
            const copy = COPY[prompt.key] ?? { label: prompt.key, help: '' }
            const draft = drafts[prompt.key] ?? ''
            const maxChars = prompt.maxChars || MAX_CHARS_FALLBACK
            const over = draft.length > maxChars
            const refusal = rejected.find((item) => item.key === prompt.key)
            const undoable = previous[prompt.key] !== undefined && previous[prompt.key] !== draft
            const setDraft = (value: string) =>
              setDrafts((current) => ({ ...current, [prompt.key]: value }))
            return (
              <Card key={prompt.key}>
                <CardHeader>
                  <div className="flex flex-wrap items-center gap-2">
                    <CardTitle>{copy.label}</CardTitle>
                    {prompt.source === 'db' ? (
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                        تغییر داده شده
                      </span>
                    ) : null}
                  </div>
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                  <p className="text-sm text-muted-foreground">{copy.help}</p>
                  {/* The card title already names which text this is; the label only has to
                      be true of all five, and two of them are not instructions. */}
                  <label htmlFor={id} className={FIELD_LABEL}>
                    متن
                  </label>
                  <textarea
                    id={id}
                    dir="rtl"
                    rows={8}
                    spellCheck={false}
                    disabled={saving}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    aria-describedby={counterId}
                    aria-invalid={over || undefined}
                    className={cn(
                      'w-full resize-y rounded-2xl border bg-background p-3 text-start text-sm leading-relaxed',
                      // 20 fixed rows was taller than a phone, which put the save button
                      // below the fold on the one screen where an unsaved edit is expensive.
                      'min-h-[40vh] lg:min-h-[30rem]',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      'disabled:opacity-50',
                      over ? 'border-destructive' : 'border-border',
                    )}
                  />
                  <p
                    id={counterId}
                    aria-live="polite"
                    className={cn(
                      'text-xs',
                      over ? 'font-medium text-destructive' : 'text-muted-foreground',
                    )}
                  >
                    {FA_NUMBER.format(draft.length)} از {FA_NUMBER.format(maxChars)} نویسه
                    {over ? ' — بیش از حد مجاز؛ متن را کوتاه کنید.' : ''}
                  </p>

                  {refusal ? (
                    <div className="rounded-2xl border border-destructive p-3 text-sm text-destructive">
                      <p className="font-medium">این متن ذخیره نشد:</p>
                      <ul className="mt-1 list-disc space-y-1 ps-5">
                        {refusal.reasons.map((reason) => (
                          <li key={reason}>{reasonText(reason)}</li>
                        ))}
                      </ul>
                      <p className="mt-2 text-xs">
                        متنی که تا پیش از این کار می‌کرد هنوز سر جایش است.
                      </p>
                    </div>
                  ) : null}

                  {prompt.requiredFragments.length > 0 ? (
                    <details className="text-xs text-muted-foreground">
                      <summary className="cursor-pointer">عبارت‌هایی که باید در متن بمانند</summary>
                      <ul className="mt-2 list-disc space-y-1 ps-5">
                        {prompt.requiredFragments.map((fragment) => (
                          <li key={fragment}>
                            <span dir="auto">{fragment}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  ) : null}

                  {prompt.forbiddenFragments.length > 0 ? (
                    <p className="text-xs text-muted-foreground">
                      این عبارت‌ها نباید در متن بیایند:{' '}
                      {prompt.forbiddenFragments.map((fragment) => (
                        <span key={fragment} dir="auto">
                          «{fragment}»{' '}
                        </span>
                      ))}
                    </p>
                  ) : null}

                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={saving || draft === prompt.default}
                      onClick={() => setDraft(prompt.default)}
                    >
                      <ArrowRotateLeft size={16} />
                      بازگرداندن به متن پیش‌فرض
                    </Button>
                    {undoable ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={saving}
                        onClick={() => setDraft(previous[prompt.key])}
                      >
                        بازگرداندن به متن پیش از ذخیره
                      </Button>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
            )
          })}

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={saving}>
              <Save2 size={18} />
              {saving ? 'در حال ذخیره…' : 'ذخیره'}
            </Button>
            {saved ? <span className="text-sm text-success">ذخیره شد.</span> : null}
          </div>
          <p className="text-xs text-muted-foreground">
            «بازگرداندن به متن پیش از ذخیره» فقط تا وقتی صفحه باز است کار می‌کند. اگر متنی رد
            شود، دستیار همان متن قبلی را نگه می‌دارد.
          </p>
        </form>
      ) : null}
    </PageContainer>
  )
}
