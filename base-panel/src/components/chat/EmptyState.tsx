'use client'

import { Book1, MagicStar, Messages2, QuoteUp, ShieldTick, type Icon } from 'iconsax-reactjs'

const EXAMPLES = [
  'چطور یک اپلیکیشن Node.js را روی لیارا مستقر کنم؟',
  'متغیرهای محیطی را در پلتفرم PaaS چگونه تنظیم کنم؟',
  'برای اتصال دامنهٔ اختصاصی و فعال‌سازی SSL چه مراحلی لازم است؟',
  'چگونه یک دیسک به اپلیکیشنم اضافه کنم و داده‌ها را ماندگار نگه دارم؟',
]

/** The rules of the game, in one line each. Anything longer belongs in the answer itself. */
const NOTES: { icon: Icon; title: string; body: string }[] = [
  { icon: Book1, title: 'دامنهٔ دانش', body: 'فقط مستندات رسمی لیارا' },
  { icon: QuoteUp, title: 'ارجاع اجباری', body: 'هر ادعا با شمارهٔ منبع' },
  { icon: ShieldTick, title: 'مرز صداقت', body: 'نبود در مستندات = گفته می‌شود' },
]

/** First-run welcome: says what this is and offers real questions to click. */
export function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center gap-5 py-6 text-center">
      <div className="gradient-primary flex size-12 items-center justify-center rounded-3xl">
        <Messages2 className="size-6" variant="Bold" aria-hidden />
      </div>

      <div className="flex flex-col gap-2">
        <h2 className="text-lg font-bold">دستیار مستندات لیارا</h2>
        <p className="text-sm leading-7 text-muted-foreground">
          پرسش‌تان دربارهٔ استقرار، پایگاه‌داده، دامنه، دیسک یا خطاهای لیارا را بپرسید. پاسخ‌ها فقط از
          مستندات رسمی ساخته می‌شوند و هر ادعا با شمارهٔ منبع همراه است.
        </p>
      </div>

      <ul className="flex flex-wrap justify-center gap-2">
        {NOTES.map((note) => (
          <li
            key={note.title}
            className="glass flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[11px] text-muted-foreground"
          >
            <note.icon className="size-3.5 shrink-0 text-primary" aria-hidden />
            <span className="font-medium text-card-foreground">{note.title}</span>
            <span>· {note.body}</span>
          </li>
        ))}
      </ul>

      <ul className="grid w-full gap-2 sm:grid-cols-2">
        {EXAMPLES.map((example) => (
          <li key={example} className="flex">
            <button
              type="button"
              onClick={() => onPick(example)}
              className="glass glass-interactive flex w-full cursor-pointer items-start gap-2 rounded-3xl p-3 text-start text-sm leading-6 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <MagicStar className="mt-1 size-4 shrink-0 text-primary" aria-hidden />
              <span>{example}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
