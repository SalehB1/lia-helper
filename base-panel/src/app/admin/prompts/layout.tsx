import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = {
  title: 'متن دستورالعمل دستیار',
  description: 'ویرایش متنی که قانون‌های پاسخ دادن دستیار را تعیین می‌کند',
  robots: { index: false, follow: false },
}

export default function PromptsLayout({ children }: { children: React.ReactNode }) {
  return children
}
