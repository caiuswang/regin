export type HeldCall = { toolUseId: string; tool: string }

declare module 'claude-code' {
  interface PluginState {
    'regin-permit': { held: HeldCall[] }
  }
}
