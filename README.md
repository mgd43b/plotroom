# plotroom
plotroom

On macOS and Linux, `plotroom connect --stdio` with a config-derived URL uses a private Unix socket
to verify the local daemon before sending credentials or MCP requests. Restart any `plotroom serve`
process that was already running before upgrading so it can publish this socket. Explicit URLs
continue to use HTTP or HTTPS as configured.
