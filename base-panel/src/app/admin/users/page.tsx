'use client'

import { useCallback, useEffect, useState } from 'react'
import { Trash, UserAdd } from 'iconsax-reactjs'
import { PageContainer } from '@/components/layout/PageContainer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { FIELD, FIELD_LABEL } from '@/components/ui/field'
import { useMe, type Me } from '@/components/auth/MeProvider'
import { ApiError, apiFetch } from '@/lib/api'

const ROLE_NAMES: Record<Me['role'], string> = { superuser: 'مدیر ارشد', admin: 'مدیر' }

const EMPTY_FORM = { username: '', displayName: '', password: '', role: 'admin' as Me['role'] }

function formatDate(iso?: string): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString('fa-IR')
}

/**
 * Who may sign in to the panel, and at what level.
 *
 * Superuser only — enforced by the server on every one of these calls. The guards that stop
 * someone locking everyone out (you cannot demote or delete yourself, and the last superuser
 * cannot be removed) also live server-side; this page just shows what it refuses.
 */
export default function UsersPage() {
  const { me, loading: loadingMe } = useMe()
  const [users, setUsers] = useState<(Me & { createdAt?: string })[]>([])
  const [form, setForm] = useState(EMPTY_FORM)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setUsers(await apiFetch<(Me & { createdAt?: string })[]>('/api/v1/users'))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'فهرست کاربران بارگذاری نشد.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (loadingMe || me?.role !== 'superuser') return
    void load()
  }, [load, me, loadingMe])

  async function run(action: () => Promise<unknown>, failure: string) {
    setBusy(true)
    setError(null)
    try {
      await action()
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failure)
    } finally {
      setBusy(false)
    }
  }

  async function create(event: React.FormEvent) {
    event.preventDefault()
    await run(async () => {
      await apiFetch('/api/v1/users', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username.trim(),
          password: form.password,
          role: form.role,
          displayName: form.displayName.trim() || null,
        }),
      })
      setForm(EMPTY_FORM)
    }, 'کاربر ساخته نشد.')
  }

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
        <h1 className="text-xl font-semibold">کاربران پنل</h1>
        <p className="text-sm text-muted-foreground">
          هر کاربر یا مدیر است یا مدیر ارشد. فقط مدیر ارشد می‌تواند کاربر بسازد و سطح
          دسترسی‌ها را عوض کند.
        </p>
      </header>

      {error ? (
        <Card className="border-destructive">
          <CardContent className="py-4 text-sm text-destructive" aria-live="polite">
            {error}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>افزودن کاربر</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={create} className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <label htmlFor="new-username" className={FIELD_LABEL}>
                  نام کاربری
                </label>
                <input
                  id="new-username"
                  value={form.username}
                  onChange={(event) => setForm({ ...form, username: event.target.value })}
                  required
                  dir="ltr"
                  autoComplete="off"
                  className={FIELD}
                />
              </div>
              <div className="flex flex-col gap-2">
                <label htmlFor="new-display" className={FIELD_LABEL}>
                  نام نمایشی
                </label>
                <input
                  id="new-display"
                  value={form.displayName}
                  onChange={(event) => setForm({ ...form, displayName: event.target.value })}
                  autoComplete="off"
                  className={FIELD}
                />
              </div>
              <div className="flex flex-col gap-2">
                <label htmlFor="new-password" className={FIELD_LABEL}>
                  رمز عبور
                </label>
                <input
                  id="new-password"
                  type="password"
                  value={form.password}
                  onChange={(event) => setForm({ ...form, password: event.target.value })}
                  required
                  minLength={12}
                  autoComplete="new-password"
                  className={FIELD}
                />
                <p className="text-xs text-muted-foreground">دست‌کم ۱۲ کاراکتر.</p>
              </div>
              <div className="flex flex-col gap-2">
                <label htmlFor="new-role" className={FIELD_LABEL}>
                  سطح دسترسی
                </label>
                <select
                  id="new-role"
                  value={form.role}
                  onChange={(event) =>
                    setForm({ ...form, role: event.target.value as Me['role'] })
                  }
                  className={FIELD}
                >
                  <option value="admin">{ROLE_NAMES.admin}</option>
                  <option value="superuser">{ROLE_NAMES.superuser}</option>
                </select>
              </div>
            </div>
            <div>
              <Button type="submit" disabled={busy}>
                <UserAdd size={18} />
                {busy ? 'در حال ساختن…' : 'ساخت کاربر'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>کاربران</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">در حال بارگذاری…</p>
          ) : users.length === 0 ? (
            <p className="text-sm text-muted-foreground">هنوز کاربری ساخته نشده است.</p>
          ) : (
            <ul className="flex flex-col gap-3">
              {users.map((user) => (
                <li
                  key={user.uuid}
                  className="flex flex-wrap items-center gap-3 rounded-2xl border border-border p-3"
                >
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-sm font-medium" dir="ltr">
                      {user.username}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {user.displayName || '—'} · ساخته شده {formatDate(user.createdAt)}
                    </span>
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {user.isActive ? 'فعال' : 'غیرفعال'}
                  </span>
                  <select
                    aria-label={`سطح دسترسی ${user.username}`}
                    value={user.role}
                    disabled={busy || user.uuid === me.uuid}
                    onChange={(event) =>
                      void run(
                        () =>
                          apiFetch(`/api/v1/users/${user.uuid}`, {
                            method: 'PATCH',
                            body: JSON.stringify({ role: event.target.value }),
                          }),
                        'سطح دسترسی عوض نشد.',
                      )
                    }
                    className={`${FIELD} w-auto`}
                  >
                    <option value="admin">{ROLE_NAMES.admin}</option>
                    <option value="superuser">{ROLE_NAMES.superuser}</option>
                  </select>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy || user.uuid === me.uuid}
                    onClick={() =>
                      void run(
                        () =>
                          apiFetch(`/api/v1/users/${user.uuid}`, {
                            method: 'PATCH',
                            body: JSON.stringify({ isActive: !user.isActive }),
                          }),
                        'وضعیت کاربر عوض نشد.',
                      )
                    }
                  >
                    {user.isActive ? 'غیرفعال کردن' : 'فعال کردن'}
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    disabled={busy || user.uuid === me.uuid}
                    onClick={() => {
                      if (!window.confirm(`کاربر «${user.username}» حذف شود؟ این کار برگشت‌پذیر نیست.`)) return
                      void run(
                        () => apiFetch(`/api/v1/users/${user.uuid}`, { method: 'DELETE' }),
                        'کاربر حذف نشد.',
                      )
                    }}
                  >
                    <Trash size={16} />
                    حذف
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </PageContainer>
  )
}
