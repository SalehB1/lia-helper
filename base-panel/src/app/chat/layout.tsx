import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = {
  title: 'گفت‌وگو',
  description: 'پرسش و پاسخ با دستیار مستندات لیارا، همراه با ارجاع به منابع رسمی',
}

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  return children
}
