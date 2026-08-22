'use client'

import * as React from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { ArrowLeft2, Copy, DocumentCode, TickCircle } from 'iconsax-reactjs'
import { writeClipboard } from '@/components/ui/CopyButton'
import { cn } from '@/lib/utils'
import { safeHref, type SourceRef } from '@/lib/api'
import { useArtifact } from './ArtifactProvider'

/** Stable identity for the default: a fresh `[]` would rebuild every override each render. */
const NO_SOURCES: SourceRef[] = []

/** Where a snippet stops being a snippet and starts being a file: past either bound the block
 *  moves to the side panel and the message shows a card instead. Tuned by eye — a full
 *  `liara.json` clears both, a three-line `liara deploy` clears neither. */
const ARTIFACT_MIN_LINES = 12
const ARTIFACT_MIN_CHARS = 600

/** True while rendering inside a fenced block, so <code> knows it is not inline. */
const InPreContext = React.createContext(false)

/** Body of the one fence that has not been closed yet — while an answer streams it grows a
 *  token at a time. A block matching it stays inline: promoting it to a card mid-stream would
 *  swap ~250px of laid-out code for a 64px row under the reader, and the card would hold a
 *  truncated snapshot. Context, not a prop: the component overrides are memoised, and rebuilding
 *  them per token would remount the whole answer. */
const OpenFenceContext = React.createContext('')

const CITATION = /(\[\d{1,2}\])/g

/** The still-open fence's body, or '' when every fence in `content` is closed. */
function openFenceBody(content: string): string {
  const lines = content.split('\n')
  let start = -1
  let marker = ''
  for (let i = 0; i < lines.length; i += 1) {
    const match = /^ {0,3}(`{3,}|~{3,})/.exec(lines[i])
    if (!match) continue
    if (start === -1) {
      start = i
      marker = match[1][0]
    } else if (match[1][0] === marker) {
      start = -1
    }
  }
  return start === -1 ? '' : lines.slice(start + 1).join('\n').replace(/\n+$/, '')
}

/** Flatten a rendered subtree back to text. rehype-highlight has already turned the fence into
 *  nested spans, so the raw string is only recoverable by walking them. */
function textOf(node: React.ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (React.isValidElement<{ children?: React.ReactNode }>(node)) return textOf(node.props.children)
  return ''
}

/** Labels that say nothing — better no label than a label reading "text". */
const UNNAMED = new Set(['text', 'plaintext', 'plain', 'txt', 'none', 'undefined'])

/** `language-json` on the inner <code> → `json`; '' when the fence named nothing useful. */
function langOf(node: React.ReactNode): string {
  const child = Array.isArray(node) ? node[0] : node
  if (!React.isValidElement<{ className?: string }>(child)) return ''
  const lang = /language-([\w+-]+)/.exec(child.props.className ?? '')?.[1] ?? ''
  return UNNAMED.has(lang) ? '' : lang
}

/** What to call a language out loud. Anything missing falls back to its own token. */
const LANG_NAMES: Record<string, string> = {
  bash: 'Bash', sh: 'Bash', shell: 'Bash', zsh: 'Bash', console: 'Shell',
  json: 'JSON', json5: 'JSON', yaml: 'YAML', yml: 'YAML', toml: 'TOML', ini: 'INI',
  dockerfile: 'Dockerfile', docker: 'Dockerfile', nginx: 'Nginx', apache: 'Apache',
  js: 'JavaScript', javascript: 'JavaScript', jsx: 'JavaScript JSX',
  ts: 'TypeScript', typescript: 'TypeScript', tsx: 'TypeScript JSX',
  py: 'Python', python: 'Python', php: 'PHP', go: 'Go', rust: 'Rust', java: 'Java',
  ruby: 'Ruby', rb: 'Ruby', sql: 'SQL', html: 'HTML', xml: 'XML', css: 'CSS', scss: 'SCSS',
  env: '.env', dotenv: '.env', diff: 'Diff', md: 'Markdown', markdown: 'Markdown',
}

/** A real filename when the body is unmistakably one, otherwise the language's name — so the
 *  card says what the code *is* instead of showing the bare token `json`. */
export function artifactName(code: string, lang: string): string {
  const body = code.trim()
  if (lang === 'dockerfile' || lang === 'docker' || /^FROM\s+\S/i.test(body)) return 'Dockerfile'
  if (body.startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(body)
      if (parsed && typeof parsed === 'object') {
        const keys = Object.keys(parsed as Record<string, unknown>)
        if (keys.includes('platform') || keys.includes('app')) return 'liara.json'
        if (keys.includes('dependencies') || keys.includes('devDependencies')) return 'package.json'
      }
    } catch {
      /* not JSON after all — fall through to the language name */
    }
  }
  const rows = body.split('\n').filter((line) => line.trim() && !line.trim().startsWith('#'))
  if (rows.length > 1 && rows.every((line) => /^[A-Z][A-Z0-9_]*=/.test(line))) return '.env'
  return LANG_NAMES[lang] ?? (lang || 'قطعه کد')
}

/** The pre that both the inline block and the artifact panel render. */
function CodeSurface({ className, children }: { className?: string; children?: React.ReactNode }) {
  return (
    <pre className={cn('text-[13px] leading-relaxed text-code-foreground', className)}>
      <InPreContext.Provider value>{children}</InPreContext.Provider>
    </pre>
  )
}

/** Stands in for a long fence: a file-sized card — name, language, length and a peek at the
 *  first lines — that sends the code to the panel beside the conversation. */
function ArtifactCard({
  code,
  lang,
  onOpen,
}: {
  code: string
  lang: string
  onOpen: (code: string, lang: string) => void
}) {
  const lines = code.split('\n')
  const peek = lines.slice(0, 3).join('\n')
  return (
    <button
      type="button"
      onClick={() => onOpen(code, lang)}
      className="glass glass-interactive my-6 block w-full max-w-sm overflow-hidden rounded-2xl text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="flex items-center gap-3 px-3 py-2.5">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-muted text-primary">
          <DocumentCode className="size-5" aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          {/* bdi, not dir on the block: the name reads LTR (so `.env` keeps its dot) while the
              line itself stays start-aligned with the one under it. */}
          <span className="block truncate text-sm font-medium text-card-foreground">
            <bdi dir="ltr">{artifactName(code, lang)}</bdi>
          </span>
          <span className="block truncate text-[11px] text-muted-foreground">
            {lang ? (
              <>
                <bdi dir="ltr">{lang}</bdi>
                {' · '}
              </>
            ) : null}
            {lines.length.toLocaleString('fa-IR')} خط
          </span>
        </span>
        {/* points at the inline-end edge, where the panel opens — no rtl flip */}
        <ArrowLeft2 className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </span>
      {/* The peek is what makes it read as a document rather than a row. */}
      <span
        dir="ltr"
        aria-hidden
        className="mask-b-from-40% block max-h-16 overflow-hidden border-t border-border bg-code px-3 pt-2 pb-3 font-mono text-[11px] leading-5 whitespace-pre text-code-foreground"
      >
        {peek}
      </span>
    </button>
  )
}

function CodeBlock({ children }: { children?: React.ReactNode }) {
  const artifact = useArtifact()
  const openFence = React.useContext(OpenFenceContext)
  const [copied, setCopied] = React.useState(false)
  const code = textOf(children).replace(/\n+$/, '')
  const lang = langOf(children)

  async function copy() {
    if (!(await writeClipboard(code))) return
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  const long = code.split('\n').length > ARTIFACT_MIN_LINES || code.length > ARTIFACT_MIN_CHARS
  // Only a closed fence becomes a card — which is also why the panel never holds a half-streamed
  // snapshot: by the time a card exists, its code is final.
  if (artifact && long && code !== openFence) {
    return <ArtifactCard code={code} lang={lang} onOpen={artifact.open} />
  }

  return (
    // Code stays LTR inside an RTL page and scrolls on its own axis.
    <div dir="ltr" className="relative my-6">
      <CodeSurface className="overflow-x-auto rounded-2xl border border-border bg-code px-3 pt-9 pb-3">
        {children}
      </CodeSurface>
      {lang ? (
        <span className="absolute start-3 top-2.5 text-[11px] font-medium text-muted-foreground select-none">
          {lang}
        </span>
      ) : null}
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? 'کپی شد' : 'کپی کد'}
        className="absolute end-2 top-2 inline-flex h-7 items-center gap-1 glass glass-interactive rounded-full px-2 text-xs text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {copied ? (
          <TickCircle className="size-3.5" variant="Bold" aria-hidden />
        ) : (
          <Copy className="size-3.5" aria-hidden />
        )}
        <span dir="rtl">{copied ? 'کپی شد' : 'کپی'}</span>
      </button>
    </div>
  )
}

function InlineCode({ className, children }: { className?: string; children?: React.ReactNode }) {
  const inPre = React.useContext(InPreContext)
  if (inPre) return <code className={cn(className, 'hljs bg-transparent p-0')}>{children}</code>
  return (
    <code dir="ltr" className="rounded bg-muted px-1.5 py-0.5 text-[0.9em] text-foreground">
      {children}
    </code>
  )
}

type CiteHover = { onActive?: (n: number | null) => void }

/** Replace `[n]` markers with a superscript link to source n. */
function withCitations(
  children: React.ReactNode,
  byNumber: Map<number, SourceRef>,
  hover: CiteHover,
): React.ReactNode {
  if (byNumber.size === 0) return children
  return React.Children.map(children, (child) => {
    if (typeof child !== 'string' || !child.includes('[')) return child
    const parts = child.split(CITATION)
    if (parts.length === 1) return child
    return parts.map((part, i) => {
      const match = /^\[(\d{1,2})\]$/.exec(part)
      const source = match ? byNumber.get(Number(match[1])) : undefined
      if (!match || !source) return part
      return (
        <sup key={i} className="mx-0.5">
          <a
            href={safeHref(source.url)}
            target="_blank"
            rel="noopener noreferrer"
            title={source.title}
            data-cite={source.n}
            onMouseEnter={() => hover.onActive?.(source.n)}
            onMouseLeave={() => hover.onActive?.(null)}
            onFocus={() => hover.onActive?.(source.n)}
            onBlur={() => hover.onActive?.(null)}
            // text-foreground, not text-primary: the resting chip sits on bg-muted and needs AA
            // at 11px. The unlayered `.cite:hover` / `.cite-active` rules still outrank it.
            className="cite rounded-full bg-muted px-1.5 text-[0.7rem] font-medium text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {/* same numeral system as the source chip below the answer */}
            {source.n.toLocaleString('fa-IR')}
          </a>
        </sup>
      )
    })
  })
}

/** Assistant markdown: GFM + syntax highlighting + inline citation links.
 *
 *  Memoised, and the citation *highlight* deliberately is not a prop: these overrides are
 *  the element types react-markdown renders, so rebuilding them on hover would remount the
 *  whole answer. Hovering only reports the number up; MessageBubble paints the match. */
function MarkdownImpl({
  content,
  sources = NO_SOURCES,
  className,
  onActiveSource,
}: {
  content: string
  sources?: SourceRef[]
  className?: string
  onActiveSource?: (n: number | null) => void
}) {
  const components = React.useMemo<Components>(() => {
    const byNumber = new Map(sources.map((s) => [s.n, s]))
    const hover = { onActive: onActiveSource }
    const cite = (children: React.ReactNode) => withCitations(children, byNumber, hover)
    return {
      // 24px between paragraphs against a 32px line — the break has to read as bigger than a
      // wrapped line, or the answer is one slab.
      p: ({ children }) => <p className="my-6 first:mt-0 last:mb-0">{cite(children)}</p>,
      li: ({ children }) => <li className="my-2 ps-1">{cite(children)}</li>,
      strong: ({ children }) => <strong className="font-semibold">{cite(children)}</strong>,
      em: ({ children }) => <em>{cite(children)}</em>,
      // Cell edges are drawn on ONE side each and skipped on the outer ones, so the wrapper
      // below owns the whole outline: `border-collapse` merges cell borders into the table's
      // own edge, which then cannot round with it.
      td: ({ children }) => (
        <td className="border-b border-e border-border px-3 py-1.5 last:border-e-0">{cite(children)}</td>
      ),
      th: ({ children }) => (
        <th className="border-b border-e border-border bg-muted px-3 py-1.5 text-start font-semibold last:border-e-0">
          {children}
        </th>
      ),
      // ps-, never pe-: in RTL the marker hangs off the start (right) edge.
      ul: ({ children }) => <ul className="my-6 list-disc ps-6 first:mt-0 last:mb-0">{children}</ul>,
      ol: ({ children }) => <ol className="my-6 list-decimal ps-6 first:mt-0 last:mb-0">{children}</ol>,
      h1: ({ children }) => <h1 className="mt-9 mb-4 text-xl font-bold first:mt-0">{cite(children)}</h1>,
      h2: ({ children }) => <h2 className="mt-9 mb-4 text-lg font-bold first:mt-0">{cite(children)}</h2>,
      h3: ({ children }) => <h3 className="mt-8 mb-3 text-base font-semibold first:mt-0">{cite(children)}</h3>,
      blockquote: ({ children }) => (
        <blockquote className="my-6 border-s-2 border-primary/50 ps-4 text-muted-foreground">{children}</blockquote>
      ),
      hr: () => <hr className="my-8 border-border" />,
      // The rounded, clipped surface every other block in the answer already uses — same
      // radius as a code fence. `overflow-x-auto` both keeps a wide table scrollable and
      // clips the corners; `border-separate` is what lets them round at all.
      table: ({ children }) => (
        <div className="my-6 overflow-x-auto rounded-2xl border border-border">
          <table className="w-full border-separate border-spacing-0 text-[13px] [&_tr:last-child>td]:border-b-0">
            {children}
          </table>
        </div>
      ),
      a: ({ href, children }) => (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {children}
        </a>
      ),
      pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
      code: ({ className: codeClass, children }) => (
        <InlineCode className={codeClass}>{children}</InlineCode>
      ),
    }
  }, [sources, onActiveSource])

  return (
    <div className={cn('text-[15px] leading-8 break-words', className)}>
      <OpenFenceContext.Provider value={openFenceBody(content)}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
          components={components}
        >
          {content}
        </ReactMarkdown>
      </OpenFenceContext.Provider>
    </div>
  )
}

export const Markdown = React.memo(MarkdownImpl)

/** Module scope so the identity is stable — these overrides are the element types. */
const PLAIN_CODE: Components = {
  pre: ({ children }) => <CodeSurface className="w-max min-w-full">{children}</CodeSurface>,
  code: ({ className, children }) => <InlineCode className={className}>{children}</InlineCode>,
}

/** Highlight a bare code string — the artifact panel, which holds text rather than markdown.
 *  Same pipeline as an inline fence, so the syntax tokens match exactly. */
export function HighlightedCode({ code, lang }: { code: string; lang?: string }) {
  // A fence longer than any backtick run in the code, so the body can never close it early.
  const ticks = Math.max(3, ...Array.from(code.matchAll(/`+/g), (m) => m[0].length + 1))
  const fence = '`'.repeat(ticks)
  return (
    <ReactMarkdown
      rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
      components={PLAIN_CODE}
    >
      {`${fence}${lang ?? ''}\n${code}\n${fence}`}
    </ReactMarkdown>
  )
}
