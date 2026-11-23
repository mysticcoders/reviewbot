# Choose which repositories to watch

The `watch` list in `config.toml` controls which repositories both queues come from. It is required, so there is no implicit scope.

## Set the list

Each entry is an owner (an organization or a user) or an `OWNER/REPO`:

```toml
watch = ["*"]                       # every repository you can access
watch = ["acme"]                    # everything owned by the acme org
watch = ["acme/api", "acme/web"]    # only these two repositories
watch = ["acme", "someone/tool"]    # an entire org plus one outside repo
```

You can also paste entries as GitHub URLs.

## Override the list for one run

Each command that reads the queues accepts options that replace the configured list for that run:

```sh
reviewbot tui --watch acme/api            # one repository
reviewbot tui -w acme -w other-org        # two orgs
reviewbot status --all                    # everything; ignore the config
```

## What to expect

- Searches include only open PRs in repositories that are not archived.
- ReviewBot searches each owner separately and merges the results. So each watched owner costs one GitHub search.
- When everything you watch has one owner, the tables show bare repository names. Otherwise they show `owner/repo`.
