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

`--checkout` is a verification argument for this local-source adapter. It must
resolve to the clean `RUSTBGPD_SOURCE` `HEAD`; bgperf2 fails with the requested
and actual revisions when they differ. Check out the desired commit in the
dedicated worktree before building rather than expecting the adapter to mutate
that worktree.

Normal, `release-prof`/jemalloc, and `release-prof`/DHAT builds all use the
root `Cargo.lock` through `cargo build --locked`. The builder refuses dirty
bgperf2 or rustbgpd worktrees. The Rust builder and Debian runtime images are
pinned by OCI index digest. The resulting image carries labels for both Git
revisions, both base-image digests, and the Rust toolchain line, and the build
prints the immutable local image ID.

Digest-pinned bases do not make Debian package installation immutable: the
Bookworm package repositories consulted by `apt-get update` can change. Each
image therefore records the exact installed package versions and tool output
in `/usr/local/share/rustbgpd-builder-provenance.txt` and
`/usr/local/share/rustbgpd-runtime-provenance.txt`. Archive both files along
with the rustbgpd daemon or image identity and any rrharness identity in the
measurement receipt:

```bash
git rev-parse HEAD
git -C "$RUSTBGPD_SOURCE" rev-parse HEAD
docker image inspect bgperf/rustbgpd \
  --format '{{.Id}} {{json .Config.Labels}}'
docker run --rm --entrypoint cat bgperf/rustbgpd \
  /usr/local/share/rustbgpd-builder-provenance.txt
docker run --rm --entrypoint cat bgperf/rustbgpd \
  /usr/local/share/rustbgpd-runtime-provenance.txt
```

For a DHAT build, use a unique tag and disable the build cache:

```bash
python3 bgperf2.py update rustbgpd --profile dhat \
  --tag bgperf/rustbgpd-dhat --no-cache
python3 bgperf2.py bench -t rustbgpd --image bgperf/rustbgpd-dhat \
  -n 2 -p 100000
```

Keep the same image and daemon process for the DHAT capture and bgperf2 CSV.

This adapter adds the missing `tester timeouts` CSV header so all 25 emitted
values have a distinct label. `test_rustbgpd.py` uses different error and
timeout sentinels to lock the final five column indices. Receipt consumers
should still archive the adapter revision with the CSV so the schema is
unambiguous.
