import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = { title: 'تنظیمات', description: 'تنظیمات پنل' }

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return children
}
