'use client'

import { useEffect, useState } from 'react'
import { Refresh2, Save2 } from 'iconsax-reactjs'
import { PageContainer } from '@/components/layout/PageContainer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ApiError, apiFetch } from '@/lib/api'

/** Mirrors the backend ProfileResponse. Every field is optional and clamped to 80 chars. */
type Profile = { platform: string | null; framework: string | null; notes: string | null }

const MAX_FIELD_CHARS = 80

const FIELDS = [
  { key: 'platform', label: 'پلتفرم', hint: 'مثلاً nodejs یا laravel' },
  { key: 'framework', label: 'فریم‌ورک', hint: 'مثلاً next.js یا express' },
  { key: 'notes', label: 'یادداشت', hint: 'هر نکته‌ای که دستیار باید بداند' },
] as const

const EMPTY: Profile = { platform: '', framework: '', notes: '' }

/**
 * The assistant remembers this profile and mentions it in its system prompt, so answers
 * arrive already shaped for the user's stack. An empty field clears it server-side.
 */
export default function SettingsPage() {
  const [form, setForm] = useState<Profile>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await apiFetch<Profile>('/api/v1/profile')
        if (alive) setForm({ ...EMPTY, ...data })
      } catch (err) {
        if (alive) setError(err instanceof ApiError ? err.message : 'پروفایل بارگذاری نشد.')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      // Sending "" is how a field is cleared; omitting it would leave it unchanged.
      const data = await apiFetch<Profile>('/api/v1/profile', {
        method: 'PATCH',
        body: JSON.stringify({
          platform: form.platform ?? '',
          framework: form.framework ?? '',
          notes: form.notes ?? '',
        }),
      })
      setForm({ ...EMPTY, ...data })
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'ذخیرهٔ پروفایل ناموفق بود.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <PageContainer size="md">
      <Card>
        <CardHeader>
          <CardTitle>پروفایل فنی</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-4 text-sm text-muted-foreground">
            دستیار از این اطلاعات برای شخصی‌سازی پاسخ‌ها استفاده می‌کند. خالی گذاشتن هر فیلد آن را
            پاک می‌کند.
          </p>
          <form className="flex flex-col gap-4" onSubmit={save}>
            {FIELDS.map((field) => (
              <div key={field.key} className="flex flex-col gap-1.5">
                <label htmlFor={`profile-${field.key}`} className="text-sm font-medium">
                  {field.label}
                </label>
                <input
                  id={`profile-${field.key}`}
                  value={form[field.key] ?? ''}
                  maxLength={MAX_FIELD_CHARS}
                  placeholder={field.hint}
                  disabled={loading}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, [field.key]: e.target.value }))
                  }
                  className="h-10 rounded-2xl border border-border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
                />
              </div>
            ))}

            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            {saved ? (
              <p className="text-sm text-muted-foreground" role="status">
                ذخیره شد.
              </p>
            ) : null}

            <Button type="submit" className="self-start" disabled={loading || saving}>
              {saving ? (
                <Refresh2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Save2 className="size-4" aria-hidden />
              )}
              ذخیره
            </Button>
          </form>
        </CardContent>
      </Card>
    </PageContainer>
  )
}
