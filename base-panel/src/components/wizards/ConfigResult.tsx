'use client'

import { ExportSquare } from 'iconsax-reactjs'
import { Markdown } from '@/components/chat/Markdown'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CopyButton } from '@/components/ui/CopyButton'
import { platformLabel, type PlatformId } from '@/components/wizards/PlatformPicker'
import { safeHref, type SourceRef } from '@/lib/api'

export type ConfigResponse = { content: string; sources: SourceRef[]; platform: PlatformId }

type Block = { kind: 'prose' | 'code'; lang: string; text: string }

const FENCE = /```([\w.+-]*)[ \t]*\r?\n([\s\S]*?)```/g

/** Strips the server-side `<docs>` envelope and splits fenced code out of the prose so the
 *  config itself can be rendered LTR with its own copy button. */
export function splitContent(raw: string): Block[] {
  const text = raw.replace(/<\/?docs[^>]*>/g, '').trim()
  const blocks: Block[] = []
  let cursor = 0
  FENCE.lastIndex = 0
  for (let match = FENCE.exec(text); match !== null; match = FENCE.exec(text)) {
    const before = text.slice(cursor, match.index).trim()
    if (before) blocks.push({ kind: 'prose', lang: '', text: before })
    const code = match[2].replace(/\s+$/, '')
    if (code) blocks.push({ kind: 'code', lang: match[1] || 'text', text: code })
    cursor = match.index + match[0].length
  }
  const rest = text.slice(cursor).trim()
  if (rest) blocks.push({ kind: 'prose', lang: '', text: rest })
  return blocks
}

/** Shared by both wizards: a numbered, clickable list of the documentation pages used. */
export function SourceLinks({ sources, title = 'منابع' }: { sources: SourceRef[]; title?: string }) {
  if (sources.length === 0) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="flex flex-col gap-2">
          {sources.map((source) => (
            <li key={`${source.n}-${source.url}`}>
              <a
                href={safeHref(source.url)}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-start gap-2 rounded-lg p-2 transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-lg bg-muted text-xs font-semibold">
                  {source.n}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">
                    {source.title}
                    {source.heading ? <span className="text-muted-foreground"> › {source.heading}</span> : null}
                  </span>
                  <span dir="ltr" className="text-muted-foreground block truncate text-xs">
                    {source.url}
                  </span>
                </span>
                <ExportSquare className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
              </a>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  )
}

export function CodeBlock({ lang, text }: { lang: string; text: string }) {
  return (
    <div className="glass rounded-2xl">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <span dir="ltr" className="text-muted-foreground font-mono text-xs">
          {lang}
        </span>
        <CopyButton text={text} />
      </div>
      <pre dir="ltr" className="overflow-x-auto p-3 text-start">
        <code className={`language-${lang} font-mono text-xs leading-relaxed`}>{text}</code>
      </pre>
    </div>
  )
}

export function ConfigResult({ result }: { result: ConfigResponse }) {
  const blocks = splitContent(result.content)
  const codeBlocks = blocks.filter((block) => block.kind === 'code')
  const wholeText = blocks.map((block) => block.text).join('\n\n')

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="flex-row items-center justify-between gap-2">
          <CardTitle>پیکربندی پیشنهادی — {platformLabel(result.platform)}</CardTitle>
          <CopyButton text={codeBlocks.length > 0 ? codeBlocks.map((b) => b.text).join('\n\n') : wholeText} label="کپی همه" />
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {codeBlocks.length === 0 && (
            <p className="text-muted-foreground rounded-lg bg-muted p-3 text-sm leading-relaxed">
              بلوک آمادهٔ کپی در پاسخ نبود؛ در ادامه گزیده‌های مستندات مرتبط با این پلتفرم آمده است.
              برای تولید فایل پیکربندی کامل، کلید مدل زبانی باید روی سرور تنظیم شود.
            </p>
          )}
          {blocks.map((block, index) =>
            block.kind === 'code' ? (
              <CodeBlock key={`code-${index}`} lang={block.lang} text={block.text} />
            ) : (
              <Markdown key={`prose-${index}`} content={block.text} sources={result.sources} />
            ),
          )}
        </CardContent>
      </Card>

      <SourceLinks sources={result.sources} />
    </div>
  )
}
