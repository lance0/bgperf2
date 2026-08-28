# rustbgpd target

The `rustbgpd` target builds the exact revision in a local rustbgpd Git
worktree. It does not clone a branch or change that worktree. Point it at a
dedicated clean checkout, then build and run it:

```bash
export RUSTBGPD_SOURCE=/path/to/clean/rustbgpd
./bgperf2.py update rustbgpd --checkout "$(git -C "$RUSTBGPD_SOURCE" rev-parse HEAD)" --no-cache
./bgperf2.py update gobgp --version 4.8.0
./bgperf2.py bench -t rustbgpd -n 10 -p 100000 --monitor-version 4.8.0
```

`--checkout` is an assertion for this adapter. It must resolve to the clean
`RUSTBGPD_SOURCE` `HEAD`; bgperf2 never checks out that ref for you. The build
also requires the bgperf2 worktree containing `rustbgpd.py` to be clean and
committed. A dirty or unreadable checkout stops before Docker starts.

The recipe uses digest-pinned Rust 1.95 and Debian Bookworm bases, the committed
Cargo lockfile, and `cargo build --workspace --release --locked`. The resulting
image labels the rustbgpd and bgperf2 Git revisions, both base identities, and
the Rust toolchain. It also retains installed tool and package versions in:

- `/usr/local/share/rustbgpd-builder-provenance.txt`
- `/usr/local/share/rustbgpd-runtime-provenance.txt`

Every run's `*.versions.json` records the configured image tag, daemon version,
and immutable image ID Docker assigned to the running target, monitor, and
tester containers. Preserve that file with the CSV and the two in-image build
records.

## Event-history mode

Set `RUSTBGPD_EVENT_HISTORY` to exactly `enabled` or `disabled` when comparing
revisions that support `[event_history]`:

```bash
RUSTBGPD_EVENT_HISTORY=disabled ./bgperf2.py bench -t rustbgpd -n 10 -p 100000
RUSTBGPD_EVENT_HISTORY=enabled  ./bgperf2.py bench -t rustbgpd -n 10 -p 100000
```

When it is unset, the block is omitted so older revisions that predate the
setting remain usable. The legacy `RUSTBGPD_EVENT_HISTORY_OFF` switch remains a
disabled-only compatibility path: any nonempty value selects `disabled`.
Combining it with `RUSTBGPD_EVENT_HISTORY=enabled` is rejected.
Revisions that predate the block must leave both variables unset.

The adapter configures the daemon's owner-only default Unix socket and reads
neighbor state with `rbgp --json neighbor`. An absent socket, empty response,
or malformed transient response produces an empty poll rather than killing the
sampling thread.

## Current boundary

This target measures policy-free convergence. It rejects generated policy,
`filter_test`, and nonempty per-neighbor filter assignments before creating the
target container rather than recording an unfiltered run under a filter label.

The monitor remains GoBGP. Build the pinned campaign version once and select it
for direct or batch runs:

```bash
./bgperf2.py update gobgp --version 4.8.0
./bgperf2.py bench -t rustbgpd --monitor-version 4.8.0
```

```yaml
targets:
  - name: rustbgpd
    monitor_version: 4.8.0
```

Direct and batch benchmark runs preflight the selected monitor image before
removing containers from the preceding run.
