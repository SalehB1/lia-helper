import * as React from 'react'
import { cn } from '@/lib/utils'

const SIZES = {
  sm: 'max-w-2xl',
  md: 'max-w-4xl',
  lg: 'max-w-6xl',
  xl: 'max-w-7xl',
  full: '',
} as const

const GAPS = { sm: 'gap-3', md: 'gap-4', lg: 'gap-6' } as const

/** The ONLY page wrapper. Pages never render their own <main> and never paint a
 *  page-level background — the body token is the surface, contrast comes from Card. */
export function PageContainer({
  size = 'lg',
  gap = 'md',
  className,
  children,
}: {
  size?: keyof typeof SIZES
  gap?: keyof typeof GAPS
  className?: string
  children: React.ReactNode
}) {
  return <div className={cn('mx-auto flex w-full flex-col', SIZES[size], GAPS[gap], className)}>{children}</div>
}
