'use client'

import { Warning2 } from 'iconsax-reactjs'
import { Markdown } from '@/components/chat/Markdown'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CopyButton } from '@/components/ui/CopyButton'
import { CodeBlock, SourceLinks, splitContent } from '@/components/wizards/ConfigResult'
import type { SourceRef } from '@/lib/api'

export type DiagnoseResponse = { signature: string; content: string; sources: SourceRef[] }

export function DiagnosisResult({ result }: { result: DiagnoseResponse }) {
  const blocks = splitContent(result.content)

  return (
    <div className="flex flex-col gap-4">
      <Card className="border-destructive">
        <CardHeader className="flex-row items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-2">
            <Warning2 className="text-destructive mt-0.5 size-5 shrink-0" aria-hidden />
            <div className="flex min-w-0 flex-col gap-1">
              <CardTitle className="text-muted-foreground text-xs font-medium">امضای خطای استخراج‌شده</CardTitle>
              <p dir="ltr" className="break-words text-start font-mono text-sm font-semibold leading-relaxed">
                {result.signature || 'خطای مشخصی در لاگ پیدا نشد'}
              </p>
            </div>
          </div>
          {result.signature ? <CopyButton text={result.signature} /> : null}
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>تحلیل و راه‌حل</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {blocks.length === 0 && (
            <p className="text-muted-foreground text-sm leading-relaxed">
              برای این لاگ توضیحی تولید نشد. منابع مرتبط را در فهرست پایین ببینید.
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

      <SourceLinks sources={result.sources} title="مستندات مرتبط" />
    </div>
  )
}
