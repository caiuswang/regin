export type InboxMessage = { id: number; title: string; type: string; createdAt: string }
export type Inbox = { unread: number; messages: InboxMessage[] }

declare module 'claude-code' {
  interface PluginState {
    'regin-band': { inbox: Inbox | null }
  }
}
