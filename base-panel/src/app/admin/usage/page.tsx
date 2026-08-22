'use client'

import { useEffect, useState } from 'react'
import { PageContainer } from '@/components/layout/PageContainer'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useMe } from '@/components/auth/MeProvider'
import { ApiError, apiFetch } from '@/lib/api'
import { cn, fa, usd } from '@/lib/utils'

/** One model's share of the bill. `costUsd` is null for a model whose price we do not know —
 *  which is not the same as free, so it is never rendered as a number. */
type ModelUsage = {
  model: string
  label: string
  turns: number
  promptTokens: number
  completionTokens: number
  costUsd: number | null
}

type Usage = {
  totalCostUsd: number
  promptTokens: number
  completionTokens: number
  turns: number
  byModel: ModelUsage[]
}

const CELL = 'p-2 text-start align-middle'

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-1 p-5">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-lg font-semibold">{value}</span>
      </CardContent>
    </Card>
  )
}

function ModelTable({ rows }: { rows: ModelUsage[] }) {
  return (
    <div className="overflow-x-auto">
      {/* The two token columns are the ones a phone has no room for and the least useful
          on one — model, answers and cost are the report. They come back at `sm`, and the
          horizontal scroller stays for the widths in between. */}
      <table className="w-full border-collapse text-sm sm:min-w-[36rem]">
        <thead>
          <tr className="border-b border-border text-xs text-muted-foreground">
            <th className={CELL} scope="col">
              مدل
            </th>
            <th className={CELL} scope="col">
              پاسخ
            </th>
            <th className={cn(CELL, 'hidden sm:table-cell')} scope="col">
              توکن ورودی
            </th>
            <th className={cn(CELL, 'hidden sm:table-cell')} scope="col">
              توکن خروجی
            </th>
            <th className={CELL} scope="col">
              هزینه (دلار)
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.model} className="border-b border-border last:border-0">
              <td className={CELL}>
                {row.label || row.model ? (
                  <span dir="ltr">{row.label || row.model}</span>
                ) : (
                  'مدل نامشخص'
                )}
              </td>
              <td className={CELL}>{fa(row.turns)}</td>
              <td className={cn(CELL, 'hidden sm:table-cell')}>{fa(row.promptTokens)}</td>
              <td className={cn(CELL, 'hidden sm:table-cell')}>{fa(row.completionTokens)}</td>
              <td className={CELL}>{row.costUsd === null ? '—' : usd(row.costUsd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * What the assistant has cost so far, all-time.
 *
 * Superuser only — refused server-side on the endpoint itself; the card below is only what
 * this page shows a regular user instead of a raw 403. The numbers come from the usage blob
 * stored on each answered message, so they cover chat turns and nothing else, and the two
 * notes on the page say so rather than letting a small total read as "nothing was spent".
 */
export default function UsagePage() {
  const { me, loading: loadingMe } = useMe()
  const [usage, setUsage] = useState<Usage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (loadingMe || me?.role !== 'superuser') return
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<Usage>('/api/v1/admin/usage')
        if (alive) {
          setUsage(data)
          setError(null)
        }
      } catch (err) {
        if (alive) setError(err instanceof ApiError ? err.message : 'گزارش مصرف بارگذاری نشد.')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [me, loadingMe])

  if (loadingMe) {
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
        <h1 className="text-xl font-semibold">هزینه و مصرف</h1>
        <p className="text-sm text-muted-foreground">
          مجموع توکن‌هایی که تا امروز مصرف شده و هزینه‌ای که بابت آن حساب شده است.
        </p>
      </header>

      {error ? (
        <Card className="border-destructive">
          <CardContent className="py-4 text-sm text-destructive" aria-live="polite">
            {error}
          </CardContent>
        </Card>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">در حال بارگذاری…</p>
      ) : usage ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="هزینهٔ کل" value={`${usd(usage.totalCostUsd)} دلار`} />
            <StatCard label="توکن ورودی" value={fa(usage.promptTokens)} />
            <StatCard label="توکن خروجی" value={fa(usage.completionTokens)} />
            <StatCard label="پاسخ‌های ثبت‌شده" value={fa(usage.turns)} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>مصرف به تفکیک مدل</CardTitle>
            </CardHeader>
            <CardContent>
              {usage.turns === 0 || usage.byModel.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  هنوز پاسخی ثبت نشده است. به‌محض اولین پاسخ دستیار، مصرف و هزینه همین‌جا
                  نمایش داده می‌شود.
                </p>
              ) : (
                <ModelTable rows={usage.byModel} />
              )}
            </CardContent>
          </Card>

          <div className="flex flex-col gap-1 text-xs text-muted-foreground">
            <p>شامل هزینهٔ دستیارهای عیب‌یابی و پیکربندی نمی‌شود؛ فقط گفت‌وگوها شمرده شده‌اند.</p>
            <p>قیمت‌ها نرخ رسمی ارائه‌دهنده است و ممکن است با صورت‌حساب دقیق کمی فرق کند.</p>
          </div>
        </>
      ) : null}
    </PageContainer>
  )
}
