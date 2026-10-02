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
neighbor state with `rbgp --json neighbor`. That socket authorizes as the
implicit `local-operator` under tier gRPC authorization, so the config carries
no `[security.grpc]` block; rustbgpd v0.63 and later refuse the retired
`enforcement = "legacy"`. An empty response before the socket is up is an
empty poll. Malformed output or a failed exec is a failed read, which the
shared sampler counts and the run's event artifact reports.

## Neighbor polling control

Set `RUSTBGPD_NEIGHBOR_POLL_MODE` to exactly `poll1` (the default), `poll5`,
or `off`. The first two read `rbgp --json neighbor` immediately, then wait
one or five seconds after each read, including failed reads. `off` starts no
target neighbor sampler and issues no neighbor CLI reads. CPU/memory and
monitor sampling continue unchanged; other daemon targets ignore this variable.

```bash
RUSTBGPD_NEIGHBOR_POLL_MODE=poll1 ./bgperf2.py bench -t rustbgpd -n 2 -p 100000 --results-dir results/poll1-a
RUSTBGPD_NEIGHBOR_POLL_MODE=off   ./bgperf2.py bench -t rustbgpd -n 2 -p 100000 --results-dir results/off-a
```

This measures the harness's own read overhead. With `off`, convergence uses
the monitor's checkpoint and the full 20-sample assurance window, rather
than the five-sample window available when both monitor and target confirm
completion. `poll5` can delay that second confirmation. A skipped read never
supplies a fresh target observation or a zero table witness. Monitor-only
completion is also named in the events artifact's `convergence_rule`.

The target freezes the effective mode when it is constructed. The CSV's
`neighbor poll mode` column and `target.neighbor_poll_mode` in
`*.versions.json` record it, including the default. The CSV column is blank
for other targets and precedes the final three provenance columns.

Use separate results directories for each mode and repetition; the mode is
not an artifact-name or batch-resume axis. Compare alternating runs under the
same benchmark lock and inspect convergence and target CPU together. No
timed `poll1`/`off` comparison is implied by support for this control.

## DHAT heap profiles

Build the heap-profiling image with `--profile dhat`, into its own tag:

```bash
./bgperf2.py update rustbgpd --checkout "$(git -C "$RUSTBGPD_SOURCE" rev-parse HEAD)" \
  --no-cache --profile dhat --tag bgperf/rustbgpd:candidate-dhat
./bgperf2.py bench -t rustbgpd -i bgperf/rustbgpd:candidate-dhat -n 2 -p 100000
```

The DHAT recipe builds `--profile release-prof --features dhat-heap` and labels
the image `org.rustbgpd.bgperf2.profile="dhat"` (release images carry
`"release"`). DHAT writes its profile only when the daemon exits cleanly, and
`docker stop` signals the container's shell rather than the daemon. So after a
run on a DHAT image, once the row and versions are recorded, bench sends the
daemon SIGTERM, waits up to 300 seconds for it to exit, and saves the profile as
`<run>.dhat-heap.json` beside the run's other results. A daemon that does not
exit in time produces a warning and no profile. Release images are left running
for investigation, as before.

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
