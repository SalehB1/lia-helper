import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = {
  title: 'کاربران پنل',
  description: 'ساخت کاربر، تغییر رمز و سطح دسترسی',
  robots: { index: false, follow: false },
}

export default function UsersLayout({ children }: { children: React.ReactNode }) {
  return children
}
