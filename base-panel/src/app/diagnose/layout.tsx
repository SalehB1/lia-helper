import type { Metadata } from 'next'

/** Client pages can't export metadata — co-locate a layout for every page. */
export const metadata: Metadata = {
  title: 'عیب‌یابی لاگ',
  description: 'استخراج امضای خطا از لاگ بیلد یا اجرا و یافتن صفحه‌های مرتبط در مستندات لیارا.',
}

export default function DiagnoseLayout({ children }: { children: React.ReactNode }) {
  return children
}
