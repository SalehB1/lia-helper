import {
  DocumentCode,
  DollarCircle,
  Health,
  MessageEdit,
  Messages2,
  People,
  Setting2,
  Setting3,
  type Icon,
} from 'iconsax-reactjs'

/** `short` is the label under the icon in the narrow rail, where the full one would truncate. */
export type NavItem = { href: string; label: string; short: string; icon: Icon }

export const NAV_ITEMS: NavItem[] = [
  { href: '/chat', label: 'گفت‌وگو', short: 'گفت‌وگو', icon: Messages2 },
  { href: '/config', label: 'تولید پیکربندی', short: 'پیکربندی', icon: DocumentCode },
  { href: '/diagnose', label: 'عیب‌یابی لاگ', short: 'عیب‌یابی', icon: Health },
  { href: '/settings', label: 'تنظیمات', short: 'تنظیمات', icon: Setting2 },
]

/** Superuser only — who may sign in, what has been spent, and how the assistant behaves.
 *
 *  Most specific first: `sectionFor` takes the first prefix match, so `/admin` listed above
 *  `/admin/users` would swallow both and title them «تنظیمات دستیار». */
export const SUPERUSER_NAV_ITEMS: NavItem[] = [
  { href: '/admin/users', label: 'کاربران پنل', short: 'کاربران', icon: People },
  { href: '/admin/usage', label: 'هزینه و مصرف', short: 'هزینه', icon: DollarCircle },
  { href: '/admin/prompts', label: 'متن دستورالعمل دستیار', short: 'دستورالعمل', icon: MessageEdit },
  { href: '/admin', label: 'تنظیمات دستیار', short: 'دستیار', icon: Setting3 },
]

/** Whether a pathname is inside a section. A section owns its own path AND everything
 *  under it: `/chat/<uuid>` is still chat, and without this the nav tile goes dark and the
 *  history panel disappears the moment a conversation is opened. */
export function isSection(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`)
}

/** The one place a pathname turns into a section — header title and sidebar both read it.
 *
 *  Searches the operator sections too, and the most specific first: `/admin/users` must not
 *  match `/admin`, and neither may fall through to the default and title the page
 *  «گفت‌وگو». */
export function sectionFor(pathname: string): NavItem {
  const all = [...SUPERUSER_NAV_ITEMS, ...NAV_ITEMS]
  return all.find((item) => isSection(pathname, item.href)) ?? NAV_ITEMS[0]
}
