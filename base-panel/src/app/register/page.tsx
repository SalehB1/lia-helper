'use client'

import { useState } from 'react'
import Link from 'next/link'
import { PageContainer } from '@/components/layout/PageContainer'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { FIELD, FIELD_LABEL } from '@/components/ui/field'
import { ApiError, apiFetch } from '@/lib/api'

const FAILED = 'ساخت حساب انجام نشد. لطفاً دوباره تلاش کنید.'

/**
 * Ask for an account.
 *
 * Nothing is granted here: the server creates the account inactive and sets no cookie, so a
 * successful submit means "your request was recorded", never "you are signed in". Saying that
 * plainly is the whole job of this page — a registrant who thinks they can sign in and then
 * gets the same rejection as a wrong password has no way to tell the two apart.
 */
export default function RegisterPage() {
  const [username, setUsername] = useState('')
  const [display, setDisplay] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [done, setDone] = useState(false)

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    setError(null)
    try {
      await apiFetch('/api/v1/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          username: username.trim(),
          password,
          displayName: display.trim() || null,
        }),
      })
      setDone(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : FAILED)
    } finally {
      setPending(false)
    }
  }

  return (
    <PageContainer size="sm">
      <Card>
        <CardHeader>
          <CardTitle>ساخت حساب</CardTitle>
        </CardHeader>
        <CardContent>
          {done ? (
            <div className="flex flex-col items-start gap-4">
              <p role="status" className="text-sm text-muted-foreground">
                حساب شما ساخته شد. به‌محض تأیید مدیر می‌توانید وارد شوید.
              </p>
              <Link
                href="/login"
                className="gradient-primary rounded-full px-4 py-2 text-sm font-medium transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                رفتن به صفحهٔ ورود
              </Link>
            </div>
          ) : (
            <>
              <p className="mb-5 text-sm text-muted-foreground">
                حساب می‌سازید و بعد از تأیید مدیر می‌توانید وارد شوید.
              </p>
              <form onSubmit={submit} className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  <label htmlFor="reg-username" className={FIELD_LABEL}>
                    نام کاربری
                  </label>
                  <input
                    id="reg-username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    type="text"
                    dir="ltr"
                    autoComplete="username"
                    required
                    disabled={pending}
                    className={FIELD}
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <label htmlFor="reg-display" className={FIELD_LABEL}>
                    نام نمایشی (اختیاری)
                  </label>
                  <input
                    id="reg-display"
                    value={display}
                    onChange={(event) => setDisplay(event.target.value)}
                    type="text"
                    autoComplete="name"
                    disabled={pending}
                    className={FIELD}
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <label htmlFor="reg-password" className={FIELD_LABEL}>
                    رمز عبور
                  </label>
                  <input
                    id="reg-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={12}
                    aria-describedby="reg-password-hint"
                    disabled={pending}
                    className={FIELD}
                  />
                  <p id="reg-password-hint" className="text-xs text-muted-foreground">
                    دست‌کم ۱۲ کاراکتر.
                  </p>
                </div>
                {error ? (
                  <p role="alert" className="text-sm text-destructive">
                    {error}
                  </p>
                ) : null}
                <Button type="submit" disabled={pending}>
                  {pending ? 'در حال ساخت…' : 'ساخت حساب'}
                </Button>
                <p className="text-sm text-muted-foreground">
                  قبلاً حساب ساخته‌اید؟{' '}
                  <Link
                    href="/login"
                    className="font-medium text-foreground underline underline-offset-4"
                  >
                    ورود
                  </Link>
                </p>
              </form>
            </>
          )}
        </CardContent>
      </Card>
    </PageContainer>
  )
}
