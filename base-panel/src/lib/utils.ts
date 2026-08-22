import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Persian digits for a count. */
export const fa = (n: number) => n.toLocaleString('fa-IR')

/** Persian digits for a dollar amount. Not `style: 'currency'` — that emits a Latin `$`
 *  inside a Persian sentence; the caller appends «دلار». The fraction bounds are
 *  load-bearing: the default rounds $0.0034 to «۰», which reads as "nothing was spent". */
export const usd = (n: number) =>
  n.toLocaleString('fa-IR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
