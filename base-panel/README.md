# base-panel — Liara Docs Assistant UI

Next.js 16 (App Router) + React 19 + Tailwind v4. Persian, RTL-first, light and dark.
The full project README — including how to run both halves — is [one level up](../README.md).

```bash
npm install
cp .env.example .env      # NEXT_PUBLIC_API_BASE=http://localhost:8000
npm run dev               # :3000
```

The backend must be running, and its `ALLOWED_ORIGINS` must contain this origin.

## Layout

```
src/
├── app/
│   ├── layout.tsx            # <html lang="fa" dir="rtl">, ThemeProvider, AppShell
│   ├── globals.css           # every color token, light + dark, Tailwind v4 @theme
│   ├── fonts/                # Vazirmatn variable woff2 — bundled, never fetched
│   ├── chat/[uuid]/          # the assistant
│   ├── config/  diagnose/    # the two wizards
│   ├── login/  register/     # rendered outside the shell chrome
│   ├── settings/             # profile
│   └── admin/{,users,usage,prompts}/   # superuser screens
├── components/
│   ├── chat/                 # ChatView, MessageList, Composer, SourcesList, Markdown…
│   ├── wizards/              # ConfigResult, DiagnosisResult, LogInput, PlatformPicker
│   ├── layout/               # AppShell (owns the only <main>), Sidebar, Header, nav.ts
│   └── ui/                   # button, card — hand-rolled, cva + tailwind-merge
├── hooks/                    # useChat, useConversations
└── lib/api.ts                # the only HTTP seam
```

## Conventions

- **`lib/api.ts` is the only place `fetch` is called.** It parses SSE frames, throws
  `ApiError` carrying the backend's Persian message, bounces a 401 to `/login`, and exports
  `safeHref()`. Never call `fetch` from a component.
- **The auth gate here is UX, not security.** The session cookie belongs to the API origin, so
  the Next server never sees it — no `proxy.ts` / `middleware.ts` gate exists or can. The
  backend's per-endpoint dependency is what actually refuses.
- **RTL means logical properties only**: `ps-`/`pe-`, `ms-`/`me-`, `text-start`/`text-end`,
  `border-e`. Never `pl-`/`pr-`/`ml-`/`mr-`/`left-`/`right-`. Code and config stay `dir="ltr"`.
- **No hex/rgb/hsl in any `.ts`/`.tsx`.** A new color goes in `:root` *and* `.dark` in
  `globals.css`, then into the `@theme inline` block.
- Pages orchestrate, hooks mutate, components render. Every page has a co-located `layout.tsx`
  exporting `metadata`. Keys come from stable ids, never the array index.
- The chat height chain prevents a double scrollbar — do not break it: `h-dvh` >
  `overflow-hidden` > `main flex-1 overflow-y-auto` > `PageContainer h-full min-h-0 flex-col` >
  list `flex-1 min-h-0 overflow-y-auto` + `shrink-0` composer.

## Checks

There is no lint script — `next lint` was removed in Next 16.

```bash
npx tsc --noEmit && npm run build
```
