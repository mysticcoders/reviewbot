# Serve the dashboard and MCP endpoint

ReviewBot can serve the two queues and your unread notifications in two ways:

- as a web page, where each PR is a link that opens in a browser that is signed in to GitHub;
- as an MCP server, so an agent can list your queue and notifications, open a PR in that browser for you, and mark notifications read.

The web page has the same three tabs as the TUI: **Review requests**, **My PRs** and **Notifications**, each with a count. Press `1`, `2` or `3` to switch, or link straight to one with `#queue`, `#mine` or `#notes` at the end of the address. On the **Notifications** tab, opening a notification or pressing **Mark read** marks it read on GitHub.

For how access is limited, read [The security model of `serve`](../explanation/security-model.md).

## 1. Add a `[serve]` table

`port`, `bind` and `browser` are required:

```toml
[serve]
port = 8765
bind = "localhost"        # or "tailnet", or "tailscale-node"
browser = "Google Chrome"
```

- On macOS, `browser` is an app name for `open -a`.
- On Linux, `browser` is a command. ReviewBot runs it with the URL as its argument.

Choose `bind`:

- `localhost`: only this machine can reach the server.
- `tailnet`: your other tailnet devices can reach the server on this machine's Tailscale address. This machine must run Tailscale.
- `tailscale-node`: on macOS only, ReviewBot gets its own device on your tailnet, with HTTPS. This machine does not need Tailscale. See [Give ReviewBot its own tailnet device](#give-reviewbot-its-own-tailnet-device-macos).

## 2. Start the server

Run it in the foreground:

```sh
reviewbot serve
```

This serves the dashboard at `/`, JSON at `/api/prs` (including `notifications`), and MCP at `/mcp`. Press `ctrl+c` to stop it.

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

## Give ReviewBot its own tailnet device (macOS)

With `bind = "tailscale-node"`, ReviewBot gets its own device on your tailnet. A small helper, `reviewbot-tsnet`, joins the tailnet as that device. It serves HTTPS with the device's certificate, and forwards requests to ReviewBot on `127.0.0.1`. For who can connect, read [The security model of `serve`](../explanation/security-model.md#binding).

Before you start, turn on MagicDNS and HTTPS certificates for your tailnet.

1. Install ReviewBot with the `tailnet` extra. The extra holds the helper, a prebuilt universal macOS binary:

   ```sh
   uv tool install --force "reviewbot[tailnet] @ git+https://github.com/mysticcoders/reviewbot"
   ```

2. Set the `[serve]` table. `hostname` is the device name on your tailnet:

   ```toml
   [serve]
   port = 8765                  # loopback port behind the device; 0 picks a free one
   bind = "tailscale-node"
   hostname = "reviewbot-brain"
   browser = "Google Chrome"
   ```

3. Approve the device once. This command prints the sign-in link and opens it:

   ```sh
   reviewbot tailnet login
   ```

4. Start the server. It serves `https://reviewbot-brain.<tailnet>.ts.net/`, and MCP at `/mcp`:

   ```sh
   reviewbot serve
   ```

5. Run `reviewbot token`. It prints the login URL and the `claude mcp add` line for that address.

If the device is not approved yet, `reviewbot serve` and `ctrl+w` in the TUI also show the sign-in link.

The first start of a new device can wait for its HTTPS certificate, because its DNS name takes time to reach Let's Encrypt. The helper tries four times, waiting 20 seconds, then 1 minute, then 2 minutes between attempts, and `reviewbot serve` or the TUI status line shows each wait. If every attempt fails, `serve` stops with the error, and `helper.log` has the details. Let's Encrypt allows five failed attempts per device name per hour, so wait before you start `serve` again.

`serve`, `token`, `tailnet login` and `tailnet logout` accept `--hostname` to override `hostname` for one run.

### Remove the device

```sh
reviewbot tailnet logout
```

This signs the device out, and deletes its local state. Tailscale still lists the device as expired. To remove it, go to the Tailscale admin console, under Machines.

### Files

- The device state is in `~/.config/reviewbot/tsnet/<hostname>/`. Only the owner can read it.
- The helper writes its log to `helper.log` in that directory. When the log is larger than 5 MB at startup, ReviewBot renames it to `helper.log.1`, which replaces any earlier one.

### Build the helper yourself

You need Go. Do one of these:

- Build a wheel: `uv run python tsnet/build_wheel.py VERSION`.
- Build the binary: `cd tsnet && go build -o ~/.local/bin/reviewbot-tsnet .`. ReviewBot finds the helper on `PATH` too.
