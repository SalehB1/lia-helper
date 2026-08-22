'use client'

import { useState } from 'react'
import { Health, Refresh2, Trash } from 'iconsax-reactjs'
import { PageContainer } from '@/components/layout/PageContainer'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { DiagnosisResult, type DiagnoseResponse } from '@/components/wizards/DiagnosisResult'
import { LogInput, MAX_LOG_CHARS, MIN_LOG_CHARS } from '@/components/wizards/LogInput'
import { ApiError, apiFetch } from '@/lib/api'

export default function DiagnosePage() {
  const [log, setLog] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<DiagnoseResponse | null>(null)

  const length = log.trim().length
  const tooShort = length < MIN_LOG_CHARS
  const tooLong = log.length > MAX_LOG_CHARS
  const canSubmit = !loading && !tooShort && !tooLong

  async function submit() {
    if (!canSubmit) return
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch<DiagnoseResponse>('/api/v1/tools/diagnose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ log }),
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
        <Health className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden />
        <p className="text-muted-foreground text-sm leading-relaxed">
          لاگ خطای بیلد یا اجرا را بچسبانید تا امضای خطا استخراج شود و صفحه‌های مرتبط مستندات لیارا کنارش بیاید.
        </p>
      </header>

      <Card>
        <CardContent className="flex flex-col gap-4 p-5">
          <LogInput value={log} onChange={setLog} disabled={loading} />

          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={submit} disabled={!canSubmit}>
              {loading && <Refresh2 className="size-4 animate-spin" aria-hidden />}
              {loading ? 'در حال تحلیل…' : 'تحلیل لاگ'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={loading || log.length === 0}
              onClick={() => {
                setLog('')
                setResult(null)
                setError(null)
              }}
            >
              <Trash className="size-4" aria-hidden />
              پاک‌کردن
            </Button>
            {tooShort && !loading && (
              <span className="text-muted-foreground text-xs">دست‌کم {MIN_LOG_CHARS} نویسه لازم است.</span>
            )}
            {tooLong && !loading && (
              <span className="text-destructive text-xs">متن از سقف مجاز بلندتر است؛ بخش انتهایی لاگ معمولاً کافی است.</span>
            )}
          </div>
        </CardContent>
      </Card>

      <section aria-live="polite" className="flex flex-col gap-4">
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
              <div className="h-4 w-1/2 animate-pulse rounded-lg bg-muted" />
              <div className="h-20 animate-pulse rounded-lg bg-muted" />
              <div className="h-4 w-1/3 animate-pulse rounded-lg bg-muted" />
            </CardContent>
          </Card>
        )}

        {!loading && !error && result && <DiagnosisResult result={result} />}

        {!loading && !error && !result && (
          <Card>
            <CardContent className="text-muted-foreground p-5 text-sm leading-relaxed">
              هنوز لاگی تحلیل نشده است. خروجی شامل امضای خطا، توضیح مبتنی بر مستندات و پیوند صفحه‌های مرتبط خواهد بود.
            </CardContent>
          </Card>
        )}
      </section>
    </PageContainer>
  )
}
