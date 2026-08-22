import type { Metadata } from 'next'
import localFont from 'next/font/local'
import './globals.css'
import { AppShell } from '@/components/layout/AppShell'
import { ConversationsProvider } from '@/components/chat/ConversationsProvider'
import { MeProvider } from '@/components/auth/MeProvider'
import { ThemeProvider } from '@/components/theme-provider'

/**
 * Vazir's maintained successor, **bundled rather than fetched**. Variable weight, so one
 * 111 KB file covers 100–900 and every glyph the panel shows — Persian, Latin and the ZWNJ.
 *
 * It used to be `next/font/google`, and that failed in the way this whole app is built to
 * avoid: silently. A font download that does not succeed at compile time does not error —
 * Next emits the metric-adjusted fallback (`@font-face { font-family: "Vazirmatn Fallback";
 * src: local(Arial) }`) and **no `@font-face` for Vazirmatn at all**, so the browser resolves
 * the family to Arial and Persian renders in whatever the system happens to pick. Nothing in
 * the build output says so. That is exactly what happened here: the production bundle carried
 * all three real faces while the dev server carried none, and the only visible symptom was
 * "the font looks wrong".
 *
 * The app is deployed on an Iranian platform, where a build-time reach for
 * fonts.googleapis.com is a coin toss. A file in the repository is not.
 *
 * `adjustFontFallback` stays at its default `'Arial'` — the same metric-adjusted fallback the
 * Google loader was generating (ascent 101.87%, descent 53.36%, size-adjust 100.66%), so the
 * pre-swap layout is unchanged.
 *
 * The licence ships beside the file: Vazirmatn is SIL OFL 1.1, which permits bundling and
 * requires the licence to travel with it.
 */
const vazirmatn = localFont({
  src: './fonts/vazirmatn-variable.woff2',
  weight: '100 900',
  display: 'swap',
  variable: '--font-vazirmatn',
})

export const metadata: Metadata = {
  title: { default: 'دستیار مستندات لیارا', template: '%s | دستیار مستندات لیارا' },
  description: 'دستیار فارسی مستندات لیارا: پاسخ مبتنی بر مستندات رسمی با ارجاع به منبع، تولید پیکربندی استقرار و عیب‌یابی لاگ.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fa" dir="rtl" className={vazirmatn.variable} suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <MeProvider>
            <ConversationsProvider>
              <AppShell>{children}</AppShell>
            </ConversationsProvider>
          </MeProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
