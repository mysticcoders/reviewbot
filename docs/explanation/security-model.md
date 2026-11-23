# The security model of `serve`

`reviewbot serve` puts your PR lists on an HTTP port, and lets MCP clients open PRs in your browser. ReviewBot limits who can reach it in layers. Each layer stops a different attack.

## Binding

- `localhost` listens on `127.0.0.1` only.
- `tailnet` listens only on the Tailscale address of this machine. Your other tailnet devices can reach it, but your local network cannot.
- `tailscale-node` keeps the server on `127.0.0.1`. The `reviewbot-tsnet` helper joins the tailnet as a separate device, and forwards requests to the server. The helper admits only requests from devices that belong to the user who approved the device. It refuses tagged devices. The token is still required.

ReviewBot never listens on every interface.

## Token

Every HTTP request needs the token. The token is in `~/.config/reviewbot/serve-token`. ReviewBot creates it at first use, readable by the owner only.

- MCP clients send `Authorization: Bearer <token>`.
- The browser opens the login URL from `reviewbot token` once. The server exchanges the token for an `HttpOnly`, `SameSite=Strict` cookie, and redirects to a URL without the token. With `tailscale-node`, the cookie is also `Secure`.

The login URL holds the token, so it stays in the browser history. To make that URL useless, rotate the token and restart the server. A running server reads the token only when it starts, so it accepts the old token until you restart it.

## Host and Origin checks

Each request must name one of the server's own host names in its `Host` header. With `tailscale-node`, the device's tailnet name is one of them. Any `Origin` header must match those names too.

These checks stop DNS rebinding. They also stop other websites in your browser from reaching the server.

## What `open_pr` can open

`open_pr` opens only a PR that is on one of the two lists. So a client cannot use it to send your browser to any other address.

## Stdio

`reviewbot mcp` needs no token. The MCP client starts it as your own process, so it has your rights already.

## What is not protected

- With `localhost` and `tailnet`, the server uses plain HTTP. With `tailnet`, Tailscale encrypts the traffic between devices. With `localhost`, the traffic does not leave the machine.
- With `tailscale-node`, the helper serves HTTPS on the tailnet. Between the helper and the server, the traffic is plain HTTP on `127.0.0.1`.
- Any process that runs as your user can read the token file.
