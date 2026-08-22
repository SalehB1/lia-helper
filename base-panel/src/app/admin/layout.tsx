import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = {
  title: 'تنظیمات دستیار',
  description: 'انتخاب مدل، سقف زمان و هزینه، و رفتار دستیار',
  robots: { index: false, follow: false },
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return children
}
