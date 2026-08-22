import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = {
  title: 'هزینه و مصرف',
  description: 'توکن‌های مصرف‌شده و هزینهٔ تخمینی دستیار',
  robots: { index: false, follow: false },
}

export default function UsageLayout({ children }: { children: React.ReactNode }) {
  return children
}
