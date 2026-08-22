'use client'

import { useCallback, useEffect, useState } from 'react'
import { Save2 } from 'iconsax-reactjs'
import { PageContainer } from '@/components/layout/PageContainer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { FIELD, FIELD_LABEL } from '@/components/ui/field'
import { useMe } from '@/components/auth/MeProvider'
import { ApiError, apiFetch } from '@/lib/api'

/** One setting, exactly as the backend describes it. `key` is already camelCase, so what is
 *  read here is what gets sent back. */
type Setting = {
  key: string
  value: number | boolean | string | string[]
  source: 'default' | 'db'
  min: number | null
  max: number | null
  options: string[] | null
  restartRequired: boolean
}

/** Wording for every setting. Each label is a sentence about what happens, not a name for a
 *  variable — a reader should not have to already know the feature to understand the line. */
const COPY: Record<string, { label: string; help: string }> = {
  modelPrimary: {
    label: 'مدل اصلی',
    help: 'همان مدلی که به همهٔ پرسش‌ها جواب می‌دهد. انتخاب مدل فقط از همین‌جاست؛ کاربر مدل خودش را انتخاب نمی‌کند.',
  },
  ladder: {
    label: 'اگر مدل اصلی جواب نداد، به همین ترتیب مدل‌های بعدی را امتحان کن',
    help: 'شناسهٔ مدل‌ها را با ویرگول و به همان ترتیبی که باید امتحان شوند بنویسید؛ شناسهٔ ناآشنا نادیده گرفته می‌شود.',
  },
  escalation: {
    label: 'وقتی مدلی جواب نمی‌دهد، مدل بعدی امتحان شود',
    help: 'اگر خاموش باشد، فقط همان مدل اصلی امتحان می‌شود.',
  },
  maxModelAttempts: {
    label: 'حداکثر چند مدل برای یک پرسش امتحان شود',
    help: 'شمارش از خودِ مدل اول شروع می‌شود.',
  },
  modelFallback: {
    label: 'مدل سرویس‌های پشتیبان',
    help: 'وقتی سرویس اصلی در دسترس نیست، درخواست با این مدل به سرویس پشتیبان می‌رود.',
  },
  reasoningEffort: {
    label: 'چقدر مدل پیش از نوشتن پاسخ فکر کند',
    help: 'فقط روی مدل‌های GPT-5 اثر دارد؛ هرچه بیشتر، پاسخ کندتر و گران‌تر.',
  },
  toolRounds: {
    label: 'مدل اصلی چند بار اجازه دارد در مستندات جست‌وجو کند',
    help: 'هر جست‌وجو یک رفت‌وبرگشت اضافه به مدل است؛ عدد بزرگ‌تر یعنی پاسخ دقیق‌تر ولی کندتر.',
  },
  retryToolRounds: {
    label: 'مدل‌های بعدی چند بار اجازه دارند جست‌وجو کنند',
    help: 'مستنداتی که مدل قبلی پیدا کرده هنوز در دسترس است، پس معمولاً عدد کم کافی است.',
  },
  turnBudgetSeconds: {
    label: 'حداکثر زمان آماده شدن یک پاسخ (ثانیه)',
    help: 'وقتی این زمان تمام شود، دستیار با همان چیزی که تا آن لحظه پیدا کرده جواب می‌دهد.',
  },
  maxLlmCalls: {
    label: 'حداکثر دفعات صدا زدن مدل برای یک پاسخ',
    help: 'سقف کل هزینهٔ یک پرسش، روی همهٔ مدل‌هایی که امتحان می‌شوند.',
  },
  attemptTimeoutSeconds: {
    label: 'حداکثر انتظار برای هر بار صدا زدن مدل (ثانیه)',
    help: 'اگر مدلی در این مدت چیزی نفرستد، رهایش می‌کنیم و سراغ مدل بعدی می‌رویم.',
  },
  agentMode: {
    label: 'جست‌وجوی خودکار در مستندات',
    help: 'روشن یعنی خود مدل تصمیم می‌گیرد کدام صفحه‌ها را بخواند؛ خاموش یعنی همیشه همان چند صفحهٔ نزدیک به پرسش را می‌گیرد.',
  },
  streamEnabled: {
    label: 'نمایش تدریجی پاسخ',
    help: 'روشن یعنی متن کلمه‌به‌کلمه نوشته می‌شود؛ خاموش یعنی پاسخ یک‌جا می‌رسد.',
  },
  autoTitle: {
    label: 'نام‌گذاری خودکار گفت‌وگوها',
    help: 'روشن باشد، بعد از اولین پرسش یک نام کوتاه برای گفت‌وگو ساخته می‌شود؛ خاموش باشد، همان چند کلمهٔ اول پرسش روی گفت‌وگو می‌ماند. کاربر در هر دو حالت می‌تواند نام را خودش عوض کند.',
  },
  handoffEnabled: {
    label: 'راهنمایی کاربری که به نتیجه نمی‌رسد به سمت پشتیبانی',
    help: 'اگر کاربر چند بار بنویسد که مشکلش حل نشده یا با لحن ناراضی بنویسد، دستیار در انتهای پاسخ همان چیزی را می‌گوید که در «متن دعوت به پشتیبانی» نوشته‌اید. تا وقتی روشنش نکنید هیچ اتفاقی نمی‌افتد.',
  },
  handoffAfter: {
    label: 'بعد از چند پیام ناراضی این کار انجام شود',
    help: 'شمارش روی پیام‌های خود کاربر در همین گفت‌وگوست. کمتر از ۲ ممکن نیست، چون «تکرار» یعنی دست‌کم دو بار.',
  },
  logLevel: {
    label: 'جزئیات لاگ',
    help: 'حالت DEBUG همه‌چیز را می‌نویسد و فقط به درد عیب‌یابی می‌خورد.',
  },
}

const SECTIONS = [
  {
    title: 'مدل‌ها',
    help: 'کدام مدل جواب می‌دهد، و اگر جواب نداد سراغ کدام برویم.',
    keys: ['modelPrimary', 'ladder', 'escalation', 'maxModelAttempts', 'modelFallback', 'reasoningEffort'],
  },
  {
    title: 'سقف زمان و هزینه',
    help: 'چقدر وقت و چند درخواست برای یک پاسخ خرج شود.',
    keys: ['toolRounds', 'retryToolRounds', 'turnBudgetSeconds', 'maxLlmCalls', 'attemptTimeoutSeconds'],
  },
  {
    title: 'رفتار دستیار',
    help: 'اینکه دستیار چطور مستندات را پیدا می‌کند و پاسخ را چطور نشان می‌دهد.',
    keys: ['agentMode', 'streamEnabled', 'autoTitle'],
  },
  {
    title: 'وقتی کاربر به نتیجه نمی‌رسد',
    help: 'متنِ خود دعوت در صفحهٔ «متن دستورالعمل دستیار» نوشته می‌شود؛ اینجا فقط تصمیم می‌گیرید که انجام شود یا نه و بعد از چند بار.',
    keys: ['handoffEnabled', 'handoffAfter'],
  },
  { title: 'لاگ سرور', help: 'چقدر جزئیات در لاگ نوشته شود.', keys: ['logLevel'] },
]

/** Options for `reasoningEffort`, whose raw values are English and mean nothing on screen. */
const EFFORT_NAMES: Record<string, string> = {
  '': 'خاموش',
  minimal: 'کمترین',
  low: 'کم',
  medium: 'متوسط',
  high: 'زیاد',
}

const RESTART_NOTE = 'این تغییر روی لاگ خودِ وب‌سرور تا راه‌اندازی دوباره اثر نمی‌کند.'

function asText(setting: Setting): string {
  if (Array.isArray(setting.value)) return setting.value.join('، ')
  return String(setting.value)
}

/** Turn one edited field back into the JSON shape the backend expects for that setting. */
function toPayload(setting: Setting, raw: string): number | string | string[] | boolean | null {
  if (typeof setting.value === 'boolean') return raw === 'true'
  if (setting.key === 'ladder') {
    const items = raw
      .split(/[,،]/)
      .map((item) => item.trim())
      .filter(Boolean)
    return items.length ? items : null
  }
  if (setting.options || typeof setting.value === 'string') return raw
  // `Number('')` is 0, not NaN. Without this an emptied field would either quietly save a
  // zero or 422 the whole request and take every other edit down with it.
  if (raw.trim() === '') return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * The settings that decide how the assistant answers.
 *
 * Superuser only — refused server-side on the endpoint itself; the card below is only what
 * this page shows a regular operator instead of a raw 403. Everything here is stored in the
 * database and applies to the next answer. The form is a
 * convenience: every value is re-checked server-side, so a number outside its range or a
 * model that is not on the allowlist is refused there whatever this page sends.
 */
export default function AdminPage() {
  const { me, loading: loadingMe } = useMe()
  const [settings, setSettings] = useState<Setting[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const apply = useCallback((list: Setting[]) => {
    setSettings(list)
    setDrafts(Object.fromEntries(list.map((setting) => [setting.key, asText(setting)])))
  }, [])

  useEffect(() => {
    if (loadingMe || me?.role !== 'superuser') return
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<{ settings: Setting[] }>('/api/v1/admin/settings')
        if (alive) apply(data.settings)
      } catch (err) {
        if (alive) setError(err instanceof ApiError ? err.message : 'تنظیمات بارگذاری نشد.')
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
    try {
      const payload: Record<string, unknown> = {}
      for (const setting of settings) {
        const draft = drafts[setting.key] ?? ''
        if (draft === asText(setting)) continue
        const value = toPayload(setting, draft)
        if (value !== null) payload[setting.key] = value
      }
      const data = await apiFetch<{ settings: Setting[]; rejected?: string[] }>(
        '/api/v1/admin/settings',
        { method: 'PUT', body: JSON.stringify(payload) },
      )
      apply(data.settings)
      if (data.rejected?.length) {
        // The server drops a value it will not accept and still answers 200. Saying so is
        // the difference between "your change is live" and "your change was ignored".
        setError(
          `این مقدارها پذیرفته نشدند و تغییر نکردند: ${data.rejected
            .map((key) => COPY[key]?.label ?? key)
            .join('، ')}`,
        )
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

  const byKey = new Map(settings.map((setting) => [setting.key, setting]))

  return (
    <PageContainer size="md">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">تنظیمات دستیار</h1>
        <p className="text-sm text-muted-foreground">
          این مقدارها در پایگاه‌داده ذخیره می‌شوند و از همان پاسخ بعدی اثر می‌گذارند.
        </p>
      </header>

      {error ? (
        <Card className="border-destructive">
          <CardContent className="py-4 text-sm text-destructive" aria-live="polite">
            {error}
          </CardContent>
        </Card>
      ) : null}

      {settings.length > 0 ? (
        <form onSubmit={save} className="flex flex-col gap-6">
          {SECTIONS.map((section) => (
            <Card key={section.title}>
              <CardHeader>
                <CardTitle>{section.title}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-5">
                <p className="text-sm text-muted-foreground">{section.help}</p>
                {section.keys.map((key) => {
                  const setting = byKey.get(key)
                  if (!setting) return null
                  const id = `setting-${key}`
                  const copy = COPY[key] ?? { label: key, help: '' }
                  const draft = drafts[key] ?? ''
                  const onChange = (value: string) =>
                    setDrafts((current) => ({ ...current, [key]: value }))
                  return (
                    <div key={key} className="flex flex-col gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <label htmlFor={id} className={FIELD_LABEL}>
                          {copy.label}
                        </label>
                        {setting.source === 'db' ? (
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                            تغییر داده شده
                          </span>
                        ) : null}
                      </div>
                      {typeof setting.value === 'boolean' ? (
                        <select
                          id={id}
                          value={draft}
                          onChange={(event) => onChange(event.target.value)}
                          className={FIELD}
                        >
                          <option value="true">روشن</option>
                          <option value="false">خاموش</option>
                        </select>
                      ) : setting.options ? (
                        <select
                          id={id}
                          value={draft}
                          onChange={(event) => onChange(event.target.value)}
                          className={FIELD}
                        >
                          {setting.options.map((option) => (
                            <option key={option} value={option}>
                              {key === 'reasoningEffort' ? (EFFORT_NAMES[option] ?? option) : option}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          id={id}
                          type={typeof setting.value === 'number' ? 'number' : 'text'}
                          value={draft}
                          min={setting.min ?? undefined}
                          max={setting.max ?? undefined}
                          dir={setting.key === 'ladder' || setting.key.startsWith('model') ? 'ltr' : undefined}
                          onChange={(event) => onChange(event.target.value)}
                          className={FIELD}
                        />
                      )}
                      {copy.help ? (
                        <p className="text-xs text-muted-foreground">{copy.help}</p>
                      ) : null}
                      {setting.restartRequired ? (
                        <p className="text-xs text-muted-foreground">{RESTART_NOTE}</p>
                      ) : null}
                      {setting.min !== null && setting.max !== null ? (
                        <p className="text-xs text-muted-foreground">
                          بازهٔ مجاز: {setting.min} تا {setting.max}
                        </p>
                      ) : null}
                    </div>
                  )
                })}
              </CardContent>
            </Card>
          ))}
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={saving}>
              <Save2 size={18} />
              {saving ? 'در حال ذخیره…' : 'ذخیره'}
            </Button>
            {saved ? <span className="text-sm text-success">ذخیره شد.</span> : null}
          </div>
        </form>
      ) : null}
    </PageContainer>
  )
}
