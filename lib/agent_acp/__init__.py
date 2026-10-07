"""Drive a `kimi acp` subprocess (feature #140).

The Agent Client Protocol is newline-delimited JSON-RPC over the child's
stdin/stdout: client requests carry an id the response settles, agent→client
reverse-RPC is answered on the same wire, and `session/update` notifications
stream the turn. `session/prompt` is a *request* — sent without an id it is
silently dropped by the SDK's dispatch (verified against the bundled
`@agentclientprotocol/sdk` source), so this client always assigns ids.
"""
