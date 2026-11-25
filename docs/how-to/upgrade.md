# Upgrade ReviewBot

How you upgrade depends on how you installed. To see the installed version, run `uv tool list`.

| Installed with | Upgrade with |
| --- | --- |
| `uv tool install git+https://github.com/mysticcoders/reviewbot` | `uv tool upgrade reviewbot`. This moves to the latest release on `main`. |
| a release tag, `...reviewbot@v0.2.0` | `uv tool install --force git+https://github.com/mysticcoders/reviewbot@v0.2.1`, with the new tag. `uv tool upgrade` keeps a tagged install where it is and reports "Nothing to upgrade". |
| `uv tool install --editable .` from a clone | `git pull` in the clone. Changes apply immediately. Reinstall only if the dependencies changed. |

Releases are tagged `vX.Y.Z` on GitHub. After each upgrade, check the version and the config:

```sh
reviewbot --version
reviewbot doctor
```

## When a release needs a new setting

A release can make a new config setting necessary. Then the old config does not load, and the error names the missing setting. At a terminal, `reviewbot doctor` offers to add it for you. Each such release has a section below.

### Upgrade to 0.5

0.5 adds filters for each view and makes the `[filters]` table required. A config from 0.4 or earlier does not load until you add it.

1. Run `reviewbot doctor` at a terminal.
2. Answer yes when it offers to add `[filters]`. It adds the table with the views as they were before, and keeps the old file as `config.toml.bak`.

To make the change by hand, add this table at the end of `~/.config/reviewbot/config.toml`:

```toml
[filters]
open = "requested"
mine = "all"
notifications = "all"
notification_kinds = ["pr", "issue"]
stale_days = 14
```

Other changes in 0.5:

- The **Review requests** tab is now **Open PRs**. With `open = "requested"` it shows the same PRs as before.
- **My PRs** also lists PRs assigned to you, with a **Role** column.
- `f` is now a TUI key. An agent, group or combat with `key = "f"` must move to another key. The same goes for `3`, which 0.4 took for the Notifications tab.
- The MCP tool `list_review_queue` is now `list_open_prs`. The old name still works.

### Keep reviews for retrospectives

Configs written before `init` turned saving on have no `[output]` table, and prompts installed before the **Recurring patterns** section existed don't ask for it. Neither changes on upgrade. To adopt both:

1. Add `[output]` to `~/.config/reviewbot/config.toml`. See [Save finished reviews](save-reviews.md).
2. Get the new `review.md` template, as described in [Prompts installed by an older version](write-prompts.md#prompts-installed-by-an-older-version).

### Upgrade to 0.2

0.2 adds Rex support and makes `multiplexer` a required setting. A config from 0.1 does not load until you add it.

1. Run `reviewbot doctor` at a terminal.
2. Answer which multiplexer you use. `doctor` adds the line, and keeps the old file as `config.toml.bak`.
3. If you chose Rex, `doctor` also offers to give each agent a `command`.

To make the change by hand, add one line near the top of `~/.config/reviewbot/config.toml`, above the first `[[agents]]`:

```toml
multiplexer = "herdr"     # keep using Herdr; nothing else changes
```

To move to Rex, write `multiplexer = "rex"` and give each agent a `command`. See [Choose a multiplexer](choose-a-multiplexer.md).
