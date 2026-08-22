import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = {
  title: 'ورود',
  description: 'ورود به بخش مدیریت دستیار مستندات لیارا',
  robots: { index: false, follow: false },
}

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return children
}
