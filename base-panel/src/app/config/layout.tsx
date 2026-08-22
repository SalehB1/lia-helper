import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = {
  title: 'تولید پیکربندی',
  description: 'ساخت فایل پیکربندی استقرار روی لیارا بر پایهٔ مستندات رسمی، همراه با ارجاع به منبع.',
}

export default function ConfigLayout({ children }: { children: React.ReactNode }) {
  return children
}
