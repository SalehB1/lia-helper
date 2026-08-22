/** Single HTTP seam for the assistant API: JSON envelope, SSE stream, signed-out bounce. */

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? '').replace(/\/+$/, '')

// Next inlines NEXT_PUBLIC_* at BUILD time. If it was not set in the panel app's env
// before `npm run build`, every call silently hits the panel's own origin and 404s.
if (!API_BASE && typeof window !== 'undefined') {
  console.error(
    'NEXT_PUBLIC_API_BASE is unset at build time — API calls will hit the panel origin and 404.',
  )
}

/**
 * Guard for an href that came from the API.
 * Source URLs originate in a third-party documentation corpus, so a poisoned page could
 * otherwise put `javascript:` into an <a href> in our own origin. Anything that is not a
 * plain https URL is dropped rather than rendered.
 */
export function safeHref(url: string | null | undefined): string | undefined {
  return url && url.startsWith('https://') ? url : undefined
}

/** A numbered documentation citation, as returned by every backend endpoint. */
export type SourceRef = { n: number; title: string; url: string; heading?: string | null }

export type ChatRole = 'user' | 'assistant'

export type ApiMessage = {
  uuid: string
  role: ChatRole
  content: string
  createdAt: string
  sources?: SourceRef[]
  /** Where this message hangs in the conversation tree; null for a root. */
  parentUuid?: string | null
  /** 1-based slot among the sibling versions of this message, and how many there are. */
  versionIndex?: number
  versionCount?: number
}

export type ConversationSummary = {
  uuid: string
  title: string
  createdAt: string
  updatedAt: string
  messageCount: number
}

export type ConversationDetail = ConversationSummary & {
  messages: ApiMessage[]
  /** A turn of this user's is generating here right now, so the transcript below is
   *  one answer short and the panel should re-attach instead of drawing it as final. */
  activeRun?: boolean
}

export class ApiError extends Error {
  code: string
  status: number

  constructor(message: string, code = 'APP_ERROR', status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

const GENERIC_ERROR = 'ارتباط با سرور برقرار نشد. لطفاً دوباره تلاش کنید.'

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra)
  h.set('Content-Type', 'application/json')
  return h
}

/** Turn a non-2xx response into an ApiError carrying the backend's Persian message. */
async function toApiError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } }
    const err = body?.error
    if (err?.message) return new ApiError(err.message, err.code ?? 'APP_ERROR', res.status)
  } catch {
    /* body was not JSON — fall through to the generic message */
  }
  return new ApiError(GENERIC_ERROR, 'HTTP_ERROR', res.status)
}

/** Every route but /healthz needs the cookie now, so any 401 means "you are signed out".
 *
 *  Two exemptions, both load-bearing. `/auth/me` is how MeProvider probes on every route and
 *  AppShell owns that redirect with a soft `router.replace`. And a 401 that arrives while the
 *  user is already standing on a public page must not navigate: ConversationsProvider mounts
 *  on /login too, so without the pathname guard it would 401 → assign → remount forever. */
const AUTH_ENDPOINTS = [
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/logout',
  '/api/v1/auth/me',
]
const PUBLIC_PAGES = ['/login', '/register']

function bounceIfSignedOut(path: string, status: number): void {
  if (status !== 401 || typeof window === 'undefined') return
  if (AUTH_ENDPOINTS.some((p) => path.startsWith(p))) return
  if (PUBLIC_PAGES.includes(window.location.pathname)) return
  // A full assign, not a router push: the signed-out document and everything it still holds
  // in memory should go away with it.
  window.location.assign('/login')
}

/** Fetch a JSON endpoint. Throws ApiError with a user-safe Persian message. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      // Before `...init`, so a caller can still override it. The panel and the API are
      // separate origins, so without this the browser sends no session cookie at all.
      credentials: 'include',
      ...init,
      headers: headers(init?.headers),
    })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw err
    throw new ApiError(GENERIC_ERROR, 'NETWORK_ERROR', 0)
  }
  bounceIfSignedOut(path, res.status)
  if (!res.ok) throw await toApiError(res)
  if (res.status === 204) return undefined as T
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export type SSEHandler = (event: string, data: any) => void // eslint-disable-line @typescript-eslint/no-explicit-any

/** Parse one `event:`/`data:` frame. Comment lines (`: ping`) are already dropped. */
function emitFrame(frame: string, onEvent: SSEHandler): void {
  let name = 'message'
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) name = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return
  const raw = dataLines.join('\n')
  try {
    onEvent(name, JSON.parse(raw))
  } catch {
    onEvent(name, { raw })
  }
}

/**
 * POST /api/v1/chat/stream and dispatch every SSE event.
 * Tolerates frames split across reads; `signal` aborts the stream cleanly.
 */
/**
 * One turn's placement in the message tree. `parentUuid`'s three states are three
 * different intents, and *omitted* is not the same as *explicitly null*:
 *
 * - omitted with `content` — a normal turn, appended under the branch's active leaf.
 * - a message uuid with empty `content` — regenerate: another answer under that same
 *   question, without storing the question twice.
 * - a message uuid, or an explicit `null`, with `content` — an edited question, kept as a
 *   sibling version of the original. `null` edits the first turn, which has no parent.
 */
export type ChatTurn = {
  conversationUuid?: string
  content?: string
  parentUuid?: string | null
  /** A question this edit replaces outright. Honoured only for a question nothing
   *  answered — see the backend's ChatRequest. */
  supersedes?: string
}

export async function streamChat(
  body: ChatTurn,
  onEvent: SSEHandler,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/v1/chat/stream`, {
      // The cookie IS the identity: the conversation is scoped to whoever it names.
      credentials: 'include',
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') return
    throw new ApiError(GENERIC_ERROR, 'NETWORK_ERROR', 0)
  }

  bounceIfSignedOut('/api/v1/chat/stream', res.status)
  if (!res.ok) throw await toApiError(res)
  if (!res.body) throw new ApiError(GENERIC_ERROR, 'NO_STREAM', res.status)

  await readSSE(res, onEvent)
}

/** Drain an SSE body, tolerating a frame split across two reads. One parser, two callers:
 *  the POST that starts a turn and the GET that re-attaches to one already running. */
async function readSSE(res: Response, onEvent: SSEHandler): Promise<void> {
  if (!res.body) throw new ApiError(GENERIC_ERROR, 'NO_STREAM', res.status)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep = buffer.indexOf('\n\n')
      while (sep !== -1) {
        emitFrame(buffer.slice(0, sep), onEvent)
        buffer = buffer.slice(sep + 2)
        sep = buffer.indexOf('\n\n')
      }
    }
    if (buffer.trim()) emitFrame(buffer, onEvent)
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') return
    throw new ApiError(GENERIC_ERROR, 'STREAM_ERROR', 0)
  } finally {
    reader.cancel().catch(() => {})
  }
}

/**
 * Re-attach to a turn that is already generating on the server.
 *
 * The turn does not belong to the connection that started it, so a refresh, a navigation or
 * a dead network drops the *view* and nothing else. This picks the same turn back up and
 * replays it from its first event, which is why a reloaded page repaints the whole answer
 * and then keeps streaming.
 *
 * @returns true when a turn was found and followed, false when there is none to follow —
 *   the ordinary case for an answer that finished while the page was away. Not an error:
 *   the caller falls back to reading the stored conversation.
 */
export async function followChat(
  conversationUuid: string,
  onEvent: SSEHandler,
  signal?: AbortSignal,
): Promise<boolean> {
  const path = `/api/v1/chat/${conversationUuid}/stream`
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, { credentials: 'include', headers: headers(), signal })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') return false
    throw new ApiError(GENERIC_ERROR, 'NETWORK_ERROR', 0)
  }

  bounceIfSignedOut(path, res.status)
  if (res.status === 404) return false
  if (!res.ok) throw await toApiError(res)
  await readSSE(res, onEvent)
  return true
}

/** Ask the server to stop writing. Closing the stream no longer does this — the turn owns
 *  its own task — so without this the stop button would only hide an answer still being
 *  paid for. Whatever was already written is kept. */
export async function stopChat(conversationUuid: string): Promise<void> {
  await apiFetch<void>(`/api/v1/chat/${conversationUuid}/stop`, { method: 'POST' })
}
