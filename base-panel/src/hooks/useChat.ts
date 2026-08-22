'use client'

import { useCallback, useReducer, useRef } from 'react'
import {
  apiFetch,
  followChat,
  stopChat,
  streamChat,
  type ApiMessage,
  type ChatRole,
  type ChatTurn,
  type ConversationDetail,
  type SourceRef,
} from '@/lib/api'

export type ChatMessage = {
  uuid: string
  role: ChatRole
  content: string
  sources?: SourceRef[]
  pending?: boolean
  createdAt?: string
  /** Where this message hangs in the tree; null for a root. */
  parentUuid?: string | null
  /** 1-based slot among this message's sibling versions, and how many exist. */
  versionIndex?: number
  versionCount?: number
  /** Set when the server continued this answer on a different model. Informational only —
   *  it never explains why, because the reason is ours and not the reader's problem. */
  notice?: string
}

export type ChatState = {
  messages: ChatMessage[]
  streaming: boolean
  loading: boolean
  error: string | null
  toolStatus: string | null
  suggestions: string[]
  conversationUuid: string | null
}

type Action =
  | { type: 'start'; userUuid: string; content: string; assistantUuid: string; now: string }
  | { type: 'loading' }
  | { type: 'regenerate'; assistantUuid: string; now: string }
  | {
      type: 'edit'
      /** The question being rewritten — the one already in the transcript. */
      replaceUuid: string
      userUuid: string
      content: string
      assistantUuid: string
      now: string
    }
  | {
      type: 'meta'
      conversationUuid: string
      userMessageUuid: string
      userUuid: string
      parentUuid?: string | null
      versionIndex?: number
      versionCount?: number
    }
  | { type: 'token'; delta: string }
  | { type: 'tool'; label: string | null }
  | { type: 'notice'; text: string }
  | { type: 'sources'; sources: SourceRef[] }
  | { type: 'suggestions'; items: string[] }
  | { type: 'done'; messageUuid?: string; versionIndex?: number; versionCount?: number }
  | { type: 'error'; message: string }
  | { type: 'load'; conversationUuid: string; messages: ChatMessage[] }
  | { type: 'reset' }

const INITIAL: ChatState = {
  messages: [],
  streaming: false,
  loading: false,
  error: null,
  toolStatus: null,
  suggestions: [],
  conversationUuid: null,
}

const STREAM_FAILED = 'پاسخ‌گویی ناتمام ماند. لطفاً دوباره تلاش کنید.'
const SWITCH_FAILED = 'نمایش این نسخه ممکن نشد. لطفاً دوباره تلاش کنید.'

/** How a turn lands in the transcript: appended, replacing the last answer, or forking a
 *  question into a new version. */
type Placement = 'append' | 'regenerate' | 'edit'

/** Replace the trailing pending assistant message. */
function patchPending(messages: ChatMessage[], patch: Partial<ChatMessage>): ChatMessage[] {
  const last = messages[messages.length - 1]
  if (!last || last.role !== 'assistant') return messages
  return [...messages.slice(0, -1), { ...last, ...patch }]
}

function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case 'start':
      return {
        ...state,
        error: null,
        suggestions: [],
        toolStatus: null,
        streaming: true,
        messages: [
          ...state.messages,
          { uuid: action.userUuid, role: 'user', content: action.content, createdAt: action.now },
          { uuid: action.assistantUuid, role: 'assistant', content: '', pending: true, createdAt: action.now },
        ],
      }
    case 'regenerate': {
      // The backend writes another answer under the SAME question, so the new version
      // takes the old one's slot instead of being appended as a second exchange.
      const last = state.messages[state.messages.length - 1]
      const body = last?.role === 'assistant' ? state.messages.slice(0, -1) : state.messages
      return {
        ...state,
        error: null,
        suggestions: [],
        toolStatus: null,
        streaming: true,
        messages: [
          ...body,
          {
            uuid: action.assistantUuid,
            role: 'assistant',
            content: '',
            pending: true,
            parentUuid: last?.parentUuid,
            // Stamped like every other optimistic message. Without it a regenerated answer
            // was the one bubble in the transcript with no time under it, until a reload
            // fetched the row the server had stamped all along.
            createdAt: action.now,
          },
        ],
      }
    }
    case 'edit': {
      // An edited question forks into a sibling version, and everything that followed the
      // original belongs to the other branch — so it leaves the transcript here too.
      // The ORIGINAL's uuid, never `userUuid`: `run` mints a fresh one for the optimistic
      // bubble, so matching on that always missed, `body` kept the whole transcript, and
      // the rewritten question was appended below the old one instead of taking its place.
      const at = state.messages.findIndex((m) => m.uuid === action.replaceUuid)
      const body = at === -1 ? state.messages : state.messages.slice(0, at)
      return {
        ...state,
        error: null,
        suggestions: [],
        toolStatus: null,
        streaming: true,
        messages: [
          ...body,
          { uuid: action.userUuid, role: 'user', content: action.content, createdAt: action.now },
          {
            uuid: action.assistantUuid,
            role: 'assistant',
            content: '',
            pending: true,
            createdAt: action.now,
          },
        ],
      }
    }
    case 'meta':
      return {
        ...state,
        conversationUuid: action.conversationUuid,
        messages: state.messages.map((m) =>
          m.uuid === action.userUuid
            ? {
                ...m,
                uuid: action.userMessageUuid || m.uuid,
                parentUuid: action.parentUuid ?? m.parentUuid,
                versionIndex: action.versionIndex ?? m.versionIndex,
                versionCount: action.versionCount ?? m.versionCount,
              }
            : m,
        ),
      }
    case 'token':
      return {
        ...state,
        toolStatus: null,
        messages: patchPending(state.messages, {
          content: (state.messages[state.messages.length - 1]?.content ?? '') + action.delta,
        }),
      }
    case 'tool':
      return { ...state, toolStatus: action.label }
    case 'notice':
      return { ...state, messages: patchPending(state.messages, { notice: action.text }) }
    case 'sources':
      return { ...state, messages: patchPending(state.messages, { sources: action.sources }) }
    case 'suggestions':
      return { ...state, suggestions: action.items }
    case 'done':
      return {
        ...state,
        streaming: false,
        toolStatus: null,
        // `run` dispatches a second, bare `done` once the stream resolves, so the version
        // fields are merged only when this one actually carries them — spreading
        // `undefined` over them would wipe what the SSE `done` just delivered.
        messages: patchPending(state.messages, {
          pending: false,
          uuid: action.messageUuid || state.messages[state.messages.length - 1]?.uuid,
          ...(action.versionIndex === undefined ? {} : { versionIndex: action.versionIndex }),
          ...(action.versionCount === undefined ? {} : { versionCount: action.versionCount }),
        }),
      }
    case 'error': {
      const last = state.messages[state.messages.length - 1]
      const dropEmpty = last?.role === 'assistant' && !last.content.trim()
      return {
        ...state,
        streaming: false,
        loading: false,
        toolStatus: null,
        error: action.message,
        messages: dropEmpty ? state.messages.slice(0, -1) : patchPending(state.messages, { pending: false }),
      }
    }
    case 'loading':
      return { ...state, loading: true, error: null }
    case 'load':
      return { ...INITIAL, conversationUuid: action.conversationUuid, messages: action.messages }
    case 'reset':
      return INITIAL
  }
}

function toChatMessage(m: ApiMessage): ChatMessage {
  return {
    uuid: m.uuid,
    role: m.role,
    content: m.content,
    sources: m.sources ?? [],
    createdAt: m.createdAt,
    parentUuid: m.parentUuid,
    versionIndex: m.versionIndex,
    versionCount: m.versionCount,
  }
}

/** Everything a turn's events do to the transcript, in one place.
 *
 *  At module scope rather than nested in `run` because two callers feed it now: the POST
 *  that starts a turn, and the re-attach that picks one up after a reload. Both must
 *  produce byte-identical state, or a refreshed answer would render differently from the
 *  one you watched arrive.
 *
 *  On the re-attach path `userUuid` is empty on purpose: the `meta` reducer matches the
 *  optimistic user bubble by that id, and after a reload the real question is already in
 *  the loaded transcript, so matching nothing is exactly right — only the conversation
 *  uuid is taken from the event. */
function applyEvent(
  dispatch: React.Dispatch<Action>,
  event: string,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any,
  ctx: { userUuid: string; conversationRef: React.RefObject<string | null> },
): void {
  switch (event) {
    case 'meta':
      ctx.conversationRef.current = data.conversationUuid ?? ctx.conversationRef.current
      dispatch({
        type: 'meta',
        conversationUuid: data.conversationUuid,
        userMessageUuid: data.userMessageUuid,
        userUuid: ctx.userUuid,
        parentUuid: data.parentUuid,
        versionIndex: data.versionIndex,
        versionCount: data.versionCount,
      })
      break
    case 'tool':
      dispatch({
        type: 'tool',
        label: data.status === 'start' ? (data.label ?? 'در حال کار…') : null,
      })
      break
    case 'notice':
      if (data.text) dispatch({ type: 'notice', text: data.text })
      break
    case 'token':
      if (data.delta) dispatch({ type: 'token', delta: data.delta })
      break
    case 'sources':
      dispatch({ type: 'sources', sources: data.sources ?? [] })
      break
    case 'suggestions':
      dispatch({ type: 'suggestions', items: data.items ?? [] })
      break
    case 'done':
      dispatch({
        type: 'done',
        messageUuid: data.messageUuid,
        versionIndex: data.versionIndex,
        versionCount: data.versionCount,
      })
      break
    case 'error':
      dispatch({ type: 'error', message: data.message || STREAM_FAILED })
      break
  }
}

/** Chat state machine: optimistic send, SSE accumulation, abortable streaming. */
export function useChat() {
  const [state, dispatch] = useReducer(reducer, INITIAL)
  const abortRef = useRef<AbortController | null>(null)
  const conversationRef = useRef<string | null>(null)
  const lastUserRef = useRef<string>('')
  // Read by the stable callbacks below, which must not be rebuilt on every token — a new
  // identity per render would remount the memoised markdown overrides of every message.
  const stateRef = useRef(state)
  stateRef.current = state

  const run = useCallback(async (turn: ChatTurn, placement: Placement, replaceUuid?: string) => {
    const assistantUuid = crypto.randomUUID()
    const userUuid = crypto.randomUUID()
    const now = new Date().toISOString()
    const content = turn.content ?? ''
    if (placement === 'regenerate') {
      dispatch({ type: 'regenerate', assistantUuid, now })
    } else if (placement === 'edit') {
      lastUserRef.current = content
      dispatch({ type: 'edit', replaceUuid: replaceUuid ?? userUuid, userUuid, content, assistantUuid, now })
    } else {
      lastUserRef.current = content
      dispatch({ type: 'start', userUuid, content, assistantUuid, now })
    }

    const controller = new AbortController()
    abortRef.current = controller

    try {
      await streamChat(
        { ...turn, conversationUuid: conversationRef.current ?? undefined },
        (event, data) => applyEvent(dispatch, event, data, { userUuid, conversationRef }),
        controller.signal,
      )
      if (!controller.signal.aborted) dispatch({ type: 'done' })
    } catch (err) {
      dispatch({ type: 'error', message: (err as Error)?.message || STREAM_FAILED })
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }, [])

  const send = useCallback(
    (content: string) => {
      const trimmed = content.trim()
      if (!trimmed || abortRef.current) return
      void run({ content: trimmed }, 'append')
    },
    [run],
  )

  /** Answer the same question again. Empty content plus the question's uuid is what tells
   *  the backend to add a sibling answer rather than store the question a second time. */
  const retry = useCallback(() => {
    if (abortRef.current) return
    const question = [...stateRef.current.messages].reverse().find((m) => m.role === 'user')
    if (question) {
      void run({ parentUuid: question.uuid, content: '' }, 'regenerate')
      return
    }
    // Nothing was persisted yet (the turn failed before `meta`), so resend it outright.
    if (lastUserRef.current) void run({ content: lastUserRef.current }, 'append')
  }, [run])

  /** Rewrite one of your own questions. It becomes a sibling version of the original and
   *  the branch continues from the new one — unless nothing ever answered it, in which case
   *  it is replaced outright and no version is minted. */
  const editMessage = useCallback(
    (uuid: string, content: string) => {
      const trimmed = content.trim()
      if (!trimmed || abortRef.current) return
      const messages = stateRef.current.messages
      const at = messages.findIndex((m) => m.uuid === uuid)
      const original = messages[at]
      if (!original || original.role !== 'user') return
      // A question at the end of the branch has no answer under it: the turn died before
      // one was written, so there is no history to preserve and «۲ / ۲» under it would be
      // offering a dead attempt as a version. `versionIndex` is the mark of a question the
      // server actually stored — naming a purely optimistic uuid would 404 the whole turn.
      const dead = original.versionIndex !== undefined && at === messages.length - 1
      void run(
        {
          // `parentUuid` must be sent even when null — omitted and null mean different
          // things to the backend, and null is how you fork the very first question.
          parentUuid: original.parentUuid ?? null,
          content: trimmed,
          ...(dead ? { supersedes: uuid } : {}),
        },
        'edit',
        uuid,
      )
    },
    [run],
  )

  /** Show a different version of a message and make its branch the active one.
   *
   *  Addressed by slot rather than by uuid: the branch response only ever carries the
   *  version on screen, so the sibling's uuid is not something the client knows. */
  const switchVersion = useCallback(async (uuid: string, versionIndex: number) => {
    const conversationUuid = conversationRef.current
    if (!conversationUuid || abortRef.current) return
    try {
      const detail = await apiFetch<ConversationDetail>(
        `/api/v1/conversations/${conversationUuid}/active`,
        { method: 'PATCH', body: JSON.stringify({ messageUuid: uuid, versionIndex }) },
      )
      dispatch({
        type: 'load',
        conversationUuid: detail.uuid,
        messages: (detail.messages ?? []).map(toChatMessage),
      })
    } catch (err) {
      dispatch({ type: 'error', message: (err as Error)?.message || SWITCH_FAILED })
    }
  }, [])

  const stop = useCallback(() => {
    // Tell the server FIRST. The turn runs on its own task now, so dropping the stream
    // stops watching and nothing else — without this, stop would hide an answer that is
    // still being written and still being paid for.
    const uuid = conversationRef.current
    if (uuid) void stopChat(uuid).catch(() => {})
    abortRef.current?.abort()
    abortRef.current = null
    dispatch({ type: 'done' })
  }, [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    conversationRef.current = null
    lastUserRef.current = ''
    dispatch({ type: 'reset' })
  }, [])

  const loadConversation = useCallback(async (uuid: string) => {
    abortRef.current?.abort()
    abortRef.current = null
    dispatch({ type: 'loading' })
    try {
      const detail = await apiFetch<ConversationDetail>(`/api/v1/conversations/${uuid}`)
      conversationRef.current = detail.uuid
      lastUserRef.current = [...(detail.messages ?? [])].reverse().find((m) => m.role === 'user')?.content ?? ''
      dispatch({
        type: 'load',
        conversationUuid: detail.uuid,
        messages: (detail.messages ?? []).map(toChatMessage),
      })
      if (detail.activeRun) {
        // An answer is being written for this conversation right now — the user refreshed
        // or came back from another tab. `regenerate` is the right dispatch for all three
        // live shapes: after a new turn or an edit the transcript ends with the question,
        // so it only appends the pending bubble; after a regenerate it ends with the OLD
        // answer, which it drops first. One dispatch, no new action.
        const assistantUuid = crypto.randomUUID()
        dispatch({ type: 'regenerate', assistantUuid, now: new Date().toISOString() })
        const controller = new AbortController()
        abortRef.current = controller
        let followed = false
        try {
          followed = await followChat(
            detail.uuid,
            (event, data) => applyEvent(dispatch, event, data, { userUuid: '', conversationRef }),
            controller.signal,
          )
        } finally {
          if (abortRef.current === controller) abortRef.current = null
        }
        // The turn finished in the gap between reading the conversation and attaching
        // to it. Read the transcript once more — inline rather than by calling back into
        // this function, so there is no path that can attach, miss, and try again.
        if (!followed) {
          const settled = await apiFetch<ConversationDetail>(`/api/v1/conversations/${uuid}`)
          dispatch({
            type: 'load',
            conversationUuid: settled.uuid,
            messages: (settled.messages ?? []).map(toChatMessage),
          })
        }
      }
    } catch (err) {
      // An unknown or foreign uuid must land on an EMPTY chat carrying the error, not on
      // the previous conversation's transcript under a URL that no longer names it — and
      // `conversationRef` must not keep pointing at that thread, or the next question
      // would silently be appended to it.
      conversationRef.current = null
      lastUserRef.current = ''
      dispatch({ type: 'reset' })
      dispatch({ type: 'error', message: (err as Error)?.message || 'بارگذاری گفت‌وگو ناموفق بود.' })
    }
  }, [])

  return { state, send, stop, reset, retry, editMessage, switchVersion, loadConversation }
}
