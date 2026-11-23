# Serve the dashboard and MCP endpoint

ReviewBot can serve the two queues in two ways:

- as a web page, where each PR is a link that opens in a browser that is signed in to GitHub;
- as an MCP server, so an agent can list your queue and open a PR in that browser for you.

For how access is limited, read [The security model of `serve`](../explanation/security-model.md).

## 1. Add a `[serve]` table

Every field is required:

```toml
[serve]
port = 8765
bind = "localhost"        # or "tailnet"
browser = "Google Chrome"
```

- On macOS, `browser` is an app name for `open -a`.
- On Linux, `browser` is a command. ReviewBot runs it with the URL as its argument.

## 2. Start the server

Run it in the foreground:

```sh
reviewbot serve
```

This serves the dashboard at `/`, JSON at `/api/prs`, and MCP at `/mcp`. Press `ctrl+c` to stop it.

Or let the TUI host it:

```sh
reviewbot tui --serve
```

In the TUI, `ctrl+w` also starts the server and opens the dashboard. Press it again to stop the server.

To override `[serve]` for one run:

```sh
reviewbot serve --bind tailnet --port 9000
```

## 3. Open the dashboard

```sh
reviewbot token
```

This prints the token, the dashboard login URL, and the MCP setup. Open the login URL once in your browser.

If you override `--bind` or `--port` for `serve`, give `token` the same options. Then the URLs it prints match the server:

```sh
reviewbot token --bind tailnet --port 9000
```

## 4. Connect an MCP client

To add the server to Claude Code on the same machine, over stdio:

```sh
claude mcp add reviewbot -- reviewbot mcp
```

Stdio needs no token, because the client starts `reviewbot mcp` as your own process.

To connect over HTTP, use the token from `reviewbot token`:

```sh
claude mcp add --transport http reviewbot http://<host>:8765/mcp \
  --header "Authorization: Bearer <token>"
```

For the tools that the server offers, see [MCP tools](../reference/mcp-tools.md).

## Replace the token

```sh
reviewbot token --rotate
```

Then do these steps:

1. Restart `reviewbot serve`, or the TUI that hosts the server. A running server reads the token only when it starts, so it keeps accepting the old token, and rejects the new one.
2. Open the new login URL in the browser.
3. Change the header in each MCP client.
