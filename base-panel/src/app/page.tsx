import { redirect } from 'next/navigation'

/** Chat is the product. There is no separate dashboard to land on. */
export default function HomePage() {
  redirect('/chat')
}
