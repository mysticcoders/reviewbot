// Command reviewbot-tsnet puts ReviewBot's local dashboard on the tailnet as its own device.
//
// It joins the tailnet with an embedded tsnet node, serves HTTPS with the node's
// certificate, admits only requests from devices owned by the node's owner, and proxies
// them to ReviewBot on a loopback port. Progress is reported to ReviewBot as JSON lines on
// stdout; the helper exits when its stdin closes, so it never outlives ReviewBot.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"tailscale.com/client/local"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
	"tailscale.com/tsnet"
)

var version = "dev"

type event struct {
	Event   string `json:"event"`
	URL     string `json:"url,omitempty"`
	DNS     string `json:"dns,omitempty"`
	Owner   string `json:"owner,omitempty"`
	Message string `json:"message,omitempty"`
}

var (
	emitMu  sync.Mutex
	emitEnc = json.NewEncoder(os.Stdout)
)

// emit writes one event line for ReviewBot to read.
func emit(e event) {
	emitMu.Lock()
	defer emitMu.Unlock()
	_ = emitEnc.Encode(e)
}

// fail reports an error event and exits.
func fail(format string, args ...any) {
	emit(event{Event: "error", Message: fmt.Sprintf(format, args...)})
	os.Exit(1)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: reviewbot-tsnet run|login|logout|version [flags]")
		os.Exit(2)
	}
	command, args := os.Args[1], os.Args[2:]
	if command == "version" {
		fmt.Println(version)
		return
	}
	flags := flag.NewFlagSet(command, flag.ExitOnError)
	hostname := flags.String("hostname", "", "tailnet device name")
	state := flags.String("state", "", "directory for the node's state")
	target := flags.String("target", "", "loopback host:port of ReviewBot (run only)")
	verbose := flags.Bool("verbose", false, "write tsnet logs to stderr")
	_ = flags.Parse(args)
	if *hostname == "" || *state == "" {
		fail("--hostname and --state are required")
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	go func() {
		_, _ = io.Copy(io.Discard, os.Stdin)
		cancel()
	}()

	srv := &tsnet.Server{Hostname: *hostname, Dir: *state, UserLogf: func(string, ...any) {}}
	if *verbose {
		srv.Logf = log.New(os.Stderr, "tsnet: ", log.LstdFlags).Printf
	} else {
		srv.Logf = func(string, ...any) {}
	}
	defer srv.Close()

	switch command {
	case "run":
		run(ctx, srv, *target)
	case "login":
		status, _ := up(ctx, srv)
		emitReady(status)
	case "logout":
		logout(ctx, srv)
	default:
		fail("unknown command %q", command)
	}
}

// up starts the node and waits until it is running, reporting sign-in links as they appear.
func up(ctx context.Context, srv *tsnet.Server) (*ipnstate.Status, *local.Client) {
	if err := srv.Start(); err != nil {
		fail("starting tsnet: %v", err)
	}
	lc, err := srv.LocalClient()
	if err != nil {
		fail("tsnet local client: %v", err)
	}
	waitCtx, stopWatching := context.WithCancel(ctx)
	defer stopWatching()
	go reportLoginURLs(waitCtx, lc)
	status, err := srv.Up(ctx)
	if err != nil {
		if ctx.Err() != nil {
			os.Exit(0)
		}
		fail("joining the tailnet: %v", err)
	}
	return status, lc
}

// reportLoginURLs emits each new sign-in link while the node waits for login.
func reportLoginURLs(ctx context.Context, lc *local.Client) {
	last := ""
	for {
		if status, err := lc.StatusWithoutPeers(ctx); err == nil {
			if status.AuthURL != "" && status.AuthURL != last {
				last = status.AuthURL
				emit(event{Event: "login", URL: status.AuthURL})
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Second):
		}
	}
}

func emitReady(status *ipnstate.Status) {
	owner := ""
	if profile, ok := status.User[status.Self.UserID]; ok {
		owner = profile.LoginName
	}
	emit(event{Event: "ready", DNS: strings.TrimSuffix(status.Self.DNSName, "."), Owner: owner})
}

// run serves HTTPS on the tailnet and proxies the owner's requests to the loopback target.
func run(ctx context.Context, srv *tsnet.Server, target string) {
	host, _, err := net.SplitHostPort(target)
	if err != nil || (host != "127.0.0.1" && host != "localhost" && host != "::1") {
		fail("--target must be a loopback host:port, not %q", target)
	}
	status, lc := up(ctx, srv)
	if status.Self.Tags != nil && status.Self.Tags.Len() > 0 {
		fail("tagged nodes are not supported; the node must be owned by a user")
	}
	owner := status.Self.UserID
	ln, err := srv.ListenTLS("tcp", ":443")
	if err != nil {
		fail("listening on the tailnet: %v", err)
	}
	certCtx, certDone := context.WithTimeout(ctx, 2*time.Minute)
	_, _, err = lc.CertPair(certCtx, strings.TrimSuffix(status.Self.DNSName, "."))
	certDone()
	if err != nil {
		fail("fetching the HTTPS certificate: %v", err)
	}
	upstream := &url.URL{Scheme: "http", Host: target}
	proxy := &httputil.ReverseProxy{
		Rewrite: func(r *httputil.ProxyRequest) {
			r.SetURL(upstream)
			r.Out.Host = r.In.Host
		},
	}
	server := &http.Server{
		Handler:           ownerOnly(lc, owner, proxy),
		ReadHeaderTimeout: 10 * time.Second,
	}
	go func() {
		if err := server.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			fail("serving: %v", err)
		}
	}()
	emitReady(status)
	<-ctx.Done()
	shutdown, done := context.WithTimeout(context.Background(), 5*time.Second)
	defer done()
	_ = server.Shutdown(shutdown)
}

// ownerOnly admits requests from untagged devices that belong to the node's owner.
func ownerOnly(lc *local.Client, owner tailcfg.UserID, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		who, err := lc.WhoIs(r.Context(), r.RemoteAddr)
		if err != nil || who.Node == nil || who.UserProfile == nil {
			http.Error(w, "unknown tailnet caller", http.StatusForbidden)
			return
		}
		if who.Node.IsTagged() || who.Node.User != owner {
			http.Error(w, "only the node owner may use ReviewBot", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// logout signs the node out so it disappears from the tailnet.
func logout(ctx context.Context, srv *tsnet.Server) {
	if err := srv.Start(); err != nil {
		fail("starting tsnet: %v", err)
	}
	lc, err := srv.LocalClient()
	if err != nil {
		fail("tsnet local client: %v", err)
	}
	logoutCtx, done := context.WithTimeout(ctx, 30*time.Second)
	defer done()
	if err := lc.Logout(logoutCtx); err != nil {
		fail("logging out: %v", err)
	}
	emit(event{Event: "logged-out"})
}
