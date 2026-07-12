# rustbgpd target

This integration is based on upstream bgperf2 revision
`17216483e779f1484ef38562fb8f6b5ea6ad4d8f`. Record this repository's exact
`git rev-parse HEAD` as the adapter identity for every published result.

The target builds a local rustbgpd checkout rather than fetching a moving
branch. Point it at a dedicated, clean worktree:

```bash
export RUSTBGPD_SOURCE=/path/to/clean/rustbgpd
python3 bgperf2.py update rustbgpd --no-cache
python3 bgperf2.py bench -t rustbgpd -n 2 -p 100000
```

Normal, `release-prof`/jemalloc, and `release-prof`/DHAT builds all use the
root `Cargo.lock` through `cargo build --locked`. The builder refuses dirty
bgperf2 or rustbgpd worktrees. The resulting image carries labels for both Git
revisions and the build prints the immutable local image ID. Archive these
along with the rustbgpd daemon or image identity and any rrharness identity in
the measurement receipt:

```bash
git rev-parse HEAD
git -C "$RUSTBGPD_SOURCE" rev-parse HEAD
docker image inspect bgperf/rustbgpd \
  --format '{{.Id}} {{json .Config.Labels}}'
```

For a DHAT build, use a unique tag and disable the build cache:

```bash
python3 bgperf2.py update rustbgpd --profile dhat \
  --tag bgperf/rustbgpd-dhat --no-cache
python3 bgperf2.py bench -t rustbgpd --image bgperf/rustbgpd-dhat \
  -n 2 -p 100000
```

Keep the same image and daemon process for the DHAT capture and bgperf2 CSV.

The upstream CSV currently has 24 header labels but 25 row values because the
`tester_timeouts` value is emitted without a label. This exact placement is
covered by `test_rustbgpd.py`; receipt consumers must either pin that schema or
reject it rather than silently shifting later columns.
