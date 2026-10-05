export type Inbox = { unread: number }

declare module 'claude-code' {
  interface PluginState {
    'regin-band': { inbox: Inbox | null }
  }
}
