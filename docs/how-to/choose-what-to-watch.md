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

## Watch GitLab

ReviewBot can also watch merge requests on GitLab servers, through the `glab` CLI. Only the **Review requests** tab shows them for now: MRs that list you as a reviewer.

1. Install `glab`, and sign in to each server:

   ```sh
   glab auth login --hostname gitlab.example.org
   ```

2. Add watch entries that start with the host. An entry can be the whole host, a group, a subgroup, or one project:

   ```toml
   watch = ["acme", "gitlab.example.org/team", "gitlab.example.org/other/sub/project"]
   ```

3. Add one `[[forges]]` entry for each host:

   ```toml
   [[forges]]
   host = "gitlab.example.org"
   kind = "gitlab"
   ```

4. Run `reviewbot doctor`. It checks that `glab` is installed and signed in to each host.

Entries without a host are GitHub entries. A watch list with only GitLab entries does not query GitHub.

A GitLab MR appears as `host/group/project!12`. For how its status is decided, see [Queue statuses](../reference/statuses.md#gitlab-merge-requests).

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
