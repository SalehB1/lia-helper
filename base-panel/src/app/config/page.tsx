'use client'

import { useState } from 'react'
import { Add, CloseCircle, DocumentCode, Refresh2 } from 'iconsax-reactjs'
import { PageContainer } from '@/components/layout/PageContainer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ConfigResult, type ConfigResponse } from '@/components/wizards/ConfigResult'
import { PlatformPicker, type PlatformId } from '@/components/wizards/PlatformPicker'
import { ApiError, apiFetch } from '@/lib/api'
import { cn } from '@/lib/utils'

/** Mirrors the server-side ConfigRequest limits so the user never eats a 422. */
const MAX_NEEDS = 10
const MAX_NEED_CHARS = 80

const SUGGESTED_NEEDS = [
  'پایگاه‌دادهٔ MySQL',
  'دیسک برای فایل‌های آپلودی',
  'متغیرهای محیطی',
  'اجرای مایگریشن هنگام استقرار',
  'دامنهٔ اختصاصی و SSL',
]

export default function ConfigPage() {
  const [platform, setPlatform] = useState<PlatformId | null>(null)
  const [needs, setNeeds] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ConfigResponse | null>(null)

  const full = needs.length >= MAX_NEEDS

  function addNeed(raw: string) {
    const value = raw.trim().slice(0, MAX_NEED_CHARS)
    if (!value || full || needs.includes(value)) return
    setNeeds((current) => [...current, value])
    setDraft('')
  }

  async function submit() {
    if (!platform || loading) return
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch<ConfigResponse>('/api/v1/tools/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ platform, needs: needs.length > 0 ? needs : null }),
      })
      setResult(data)
    } catch (err) {
      setResult(null)
      setError(err instanceof ApiError ? err.message : 'ارتباط با سرور برقرار نشد. دوباره تلاش کنید.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <PageContainer>
      {/* the app header owns the page title — repeating it here said nothing new */}
      <header className="flex items-start gap-2">
        <DocumentCode className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden />
        <p className="text-muted-foreground text-sm leading-relaxed">
          پلتفرم برنامه را انتخاب کنید تا بر پایهٔ مستندات رسمی لیارا، پیکربندی استقرار به‌همراه منابع آن ساخته شود.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle id="platform-label">۱. پلتفرم</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <PlatformPicker value={platform} onChange={setPlatform} disabled={loading} labelledBy="platform-label" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>۲. نیازمندی‌ها (اختیاری)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="need-input" className="text-sm font-medium">
              افزودن نیازمندی
            </label>
            <div className="flex gap-2">
              <input
                id="need-input"
                value={draft}
                maxLength={MAX_NEED_CHARS}
                disabled={loading || full}
                placeholder="مثلاً: اتصال به پایگاه‌دادهٔ PostgreSQL"
                aria-describedby="need-hint"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    addNeed(draft)
                  }
                }}
                className="h-10 min-w-0 flex-1 rounded-2xl border border-border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              />
              <Button type="button" variant="outline" disabled={loading || full || !draft.trim()} onClick={() => addNeed(draft)}>
                <Add className="size-4" aria-hidden />
                افزودن
              </Button>
            </div>
            <p id="need-hint" className="text-muted-foreground text-xs">
              حداکثر {MAX_NEEDS} مورد، هر کدام تا {MAX_NEED_CHARS} نویسه.
              {full ? ' سقف تکمیل شده است.' : ''}
            </p>
          </div>

          {needs.length > 0 && (
            <ul className="flex flex-wrap gap-2">
              {needs.map((need) => (
                <li key={need}>
                  <span className="flex items-center gap-1 rounded-lg bg-muted px-2 py-1 text-xs">
                    {need}
                    <button
                      type="button"
                      aria-label={`حذف ${need}`}
                      disabled={loading}
                      onClick={() => setNeeds((current) => current.filter((item) => item !== need))}
                      className="rounded-lg p-0.5 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                    >
                      <CloseCircle className="size-3.5" aria-hidden />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground text-xs">پیشنهادها:</span>
            {SUGGESTED_NEEDS.filter((need) => !needs.includes(need)).map((need) => (
              <button
                key={need}
                type="button"
                disabled={loading || full}
                onClick={() => addNeed(need)}
                className="rounded-lg border border-border px-2 py-1 text-xs transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              >
                + {need}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={submit} disabled={!platform || loading}>
          {loading && <Refresh2 className="size-4 animate-spin" aria-hidden />}
          {loading ? 'در حال آماده‌سازی…' : 'ساخت پیکربندی'}
        </Button>
        {!platform && <span className="text-muted-foreground text-xs">ابتدا یک پلتفرم انتخاب کنید.</span>}
      </div>

      <section aria-live="polite" className={cn('flex flex-col gap-4', loading && 'opacity-90')}>
        {error && (
          <Card className="border-destructive">
            <CardContent className="flex flex-col gap-3 p-5">
              <p className="text-destructive text-sm font-medium">{error}</p>
              <Button type="button" variant="outline" className="self-start" onClick={submit}>
                تلاش دوباره
              </Button>
            </CardContent>
          </Card>
        )}

        {loading && (
          <Card>
            <CardContent className="flex flex-col gap-3 p-5">
              <div className="h-4 w-1/3 animate-pulse rounded-lg bg-muted" />
              <div className="h-24 animate-pulse rounded-lg bg-muted" />
              <div className="h-4 w-2/3 animate-pulse rounded-lg bg-muted" />
            </CardContent>
          </Card>
        )}

        {!loading && !error && result && <ConfigResult result={result} />}

        {!loading && !error && !result && (
          <Card>
            <CardContent className="text-muted-foreground p-5 text-sm leading-relaxed">
              هنوز پیکربندی‌ای ساخته نشده است. پس از انتخاب پلتفرم، خروجی به‌همراه فهرست صفحه‌های مستنداتی که از آن‌ها
              استفاده شده در همین بخش نمایش داده می‌شود.
            </CardContent>
          </Card>
        )}
      </section>
    </PageContainer>
  )
}
