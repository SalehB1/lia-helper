'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { PageContainer } from '@/components/layout/PageContainer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { FIELD, FIELD_LABEL } from '@/components/ui/field'
import { useMe } from '@/components/auth/MeProvider'
import { ApiError, apiFetch } from '@/lib/api'

const FAILED = 'ورود انجام نشد. لطفاً دوباره تلاش کنید.'

/**
 * Sign in. Every page of the panel but this one and /register needs an account.
 *
 * The server sets an httpOnly cookie, so nothing here ever touches a token — which is the
 * point: a script injected into this page has no way to read the session. The `refresh()`
 * result is checked rather than assumed: if the cookie never arrived (the COOKIE_SECURE /
 * COOKIE_SAMESITE deploy footgun) the user sees an error here instead of being bounced
 * straight back by a destination that cannot tell who they are.
 */
export default function LoginPage() {
  const router = useRouter()
  const { refresh } = useMe()
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setPending(true)
    setError(null)
    try {
      await apiFetch('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username: String(form.get('username') ?? ''),
          password: String(form.get('password') ?? ''),
        }),
      })
      const user = await refresh()
      if (!user) {
        setError(FAILED)
        setPending(false)
        return
      }
      router.replace('/chat')
      // `pending` stays true through the navigation: re-enabling the button for the instant
      // before the new page paints just invites a second submit.
    } catch (err) {
      setError(err instanceof ApiError ? err.message : FAILED)
      setPending(false)
    }
  }

  return (
    <PageContainer size="sm">
      <Card>
        <CardHeader>
          <CardTitle>ورود به دستیار</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-5 text-sm text-muted-foreground">برای استفاده از دستیار وارد شوید.</p>
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <label htmlFor="username" className={FIELD_LABEL}>
                نام کاربری
              </label>
              <input
                id="username"
                name="username"
                type="text"
                autoComplete="username"
                required
                disabled={pending}
                className={FIELD}
              />
            </div>
            <div className="flex flex-col gap-2">
              <label htmlFor="password" className={FIELD_LABEL}>
                رمز عبور
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                disabled={pending}
                className={FIELD}
              />
            </div>
            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}
            <Button type="submit" disabled={pending}>
              {pending ? 'در حال ورود…' : 'ورود'}
            </Button>
            <p className="text-sm text-muted-foreground">
              حساب ندارید؟{' '}
              <Link href="/register" className="font-medium text-foreground underline underline-offset-4">
                ساخت حساب
              </Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </PageContainer>
  )
}
