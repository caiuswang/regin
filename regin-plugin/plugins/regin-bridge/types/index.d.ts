export type RunningTurn = string | null

declare module 'claude-code' {
  interface PluginState {
    'regin-bridge': { turn: RunningTurn }
  }
}
