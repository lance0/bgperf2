from base import *
from settings import dckr
import json
import io
import os

# Default path to rustbgpd source tree for local builds
RUSTBGPD_SOURCE = os.environ.get('RUSTBGPD_SOURCE', '/home/lance/projects/rustbgpd')

DOCKERFILE_CONTENT = '''\
FROM rust:1.95-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
RUN cargo build --workspace --release

FROM debian:bookworm-slim
WORKDIR /root

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release/rustbgpd /usr/local/bin/rustbgpd
COPY --from=builder /build/target/release/rbgp /usr/local/bin/rustbgpctl

RUN mkdir -p /var/lib/rustbgpd
'''

DOCKERFILE_CONTENT_PROF = '''\
FROM rust:1.95-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
RUN cargo build --workspace --profile release-prof --features jemalloc

FROM debian:bookworm-slim
WORKDIR /root

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release-prof/rustbgpd /usr/local/bin/rustbgpd
COPY --from=builder /build/target/release-prof/rbgp /usr/local/bin/rustbgpctl

RUN mkdir -p /var/lib/rustbgpd
'''

DOCKERFILE_CONTENT_DHAT = '''\
FROM rust:1.95-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
RUN cargo build --workspace --profile release-prof --features dhat-heap

FROM debian:bookworm-slim
WORKDIR /root

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release-prof/rustbgpd /usr/local/bin/rustbgpd
COPY --from=builder /build/target/release-prof/rbgp /usr/local/bin/rustbgpctl

RUN mkdir -p /var/lib/rustbgpd
'''


class RustBGPd(Container):
    CONTAINER_NAME = None
    GUEST_DIR = '/root/config'

    def __init__(self, host_dir, conf, image='bgperf/rustbgpd'):
        super(RustBGPd, self).__init__(self.CONTAINER_NAME, image, host_dir, self.GUEST_DIR, conf)

    @classmethod
    def build_image(cls, force=False, tag='bgperf/rustbgpd', checkout='', nocache=False, profile=False):
        """Build rustbgpd Docker image from the local source tree.

        Set RUSTBGPD_SOURCE env var to override the source path
        (default: /home/lance/projects/rustbgpd).

        If profile=True, builds with jemalloc heap profiling enabled.
        """
        source = RUSTBGPD_SOURCE
        if not os.path.isdir(source):
            print('rustbgpd source not found at {}, skipping build'.format(source))
            return

        if not force and img_exists(tag):
            return

        if profile == 'dhat':
            content = DOCKERFILE_CONTENT_DHAT
        elif profile:
            content = DOCKERFILE_CONTENT_PROF
        else:
            content = DOCKERFILE_CONTENT

        # Write a Dockerfile into the source tree temporarily
        dockerfile_path = os.path.join(source, 'Dockerfile.bgperf')
        try:
            with open(dockerfile_path, 'w') as f:
                f.write(content)

            print('build {}... (from {}{})'.format(tag, source, ', profile=True' if profile else ''))
            for line in dckr.build(path=source, dockerfile='Dockerfile.bgperf',
                                   rm=True, tag=tag, decode=True, nocache=nocache):
                if 'stream' in line:
                    print(line['stream'].strip())
                if 'errorDetail' in line:
                    print(line['errorDetail'])
        finally:
            # Clean up the temp dockerfile
            if os.path.exists(dockerfile_path):
                os.remove(dockerfile_path)


class RustBGPdTarget(RustBGPd, Target):

    CONTAINER_NAME = 'bgperf_rustbgpd_target'
    CONFIG_FILE_NAME = 'config.toml'

    def write_config(self):
        config = '[global]\n'
        config += 'asn = {}\n'.format(self.conf['as'])
        config += 'router_id = "{}"\n'.format(self.conf['router-id'])
        config += 'listen_port = 179\n'
        config += '\n'
        config += '[global.telemetry]\n'
        config += 'prometheus_addr = "0.0.0.0:9179"\n'
        config += 'log_format = "json"\n'
        config += '\n'
        # No gRPC listener or [security.grpc] block: rustbgpd (v0.63+)
        # synthesizes an owner-only UDS socket and grants the implicit
        # local-operator identity, which is all the in-container state
        # queries need. (enforcement = "legacy" was removed in v0.63.)

        # The durable event-history outbox (ADR-0072) is default-on and
        # persists every route event to SQLite. Set RUSTBGPD_EVENT_HISTORY_OFF=1
        # to measure baseline daemon RSS without it (the v0.30-era comparison
        # point predates event-history entirely).
        if os.environ.get('RUSTBGPD_EVENT_HISTORY_OFF'):
            config += '[event_history]\n'
            config += 'enabled = false\n'
            config += '\n'

        neighbors = list(flatten(
            list(t.get('neighbors', {}).values())
            for t in self.scenario_global_conf['testers']
        )) + [self.scenario_global_conf['monitor']]

        for n in neighbors:
            config += '[[neighbors]]\n'
            config += 'address = "{}"\n'.format(n['local-address'])
            config += 'remote_asn = {}\n'.format(n['as'])
            config += '\n'

        with open('{0}/{1}'.format(self.host_dir, self.CONFIG_FILE_NAME), 'w') as f:
            f.write(config)

    def get_startup_cmd(self):
        lines = ['#!/bin/bash', 'ulimit -n 65536']
        if os.environ.get('RUSTBGPD_HEAP_PROF'):
            lines.append(
                'export _RJEM_MALLOC_CONF="prof:true,prof_final:true,'
                'prof_prefix:{guest_dir}/jeprof"')
        lines.append(
            'cd {guest_dir} && exec rustbgpd {guest_dir}/{config_file_name} > {guest_dir}/rustbgpd.log 2>&1')
        return '\n'.join(lines).format(
            guest_dir=self.guest_dir,
            config_file_name=self.CONFIG_FILE_NAME)

    def get_version_cmd(self):
        return "rustbgpd --version"

    def exec_version_cmd(self):
        ret = super().exec_version_cmd()
        return ret.strip()

    def get_neighbors_state(self, dckr_override=None):
        """Query neighbor state via rustbgpctl.

        Returns (neighbors_received, neighbors_accepted) dicts keyed by
        neighbor address.  On any failure (timeout, parse error, empty
        output) returns (None, None) so callers can distinguish "query
        failed" from "zero neighbors."
        """
        import time as _time
        t0 = _time.monotonic()
        try:
            output = self.local(
                'rustbgpctl --json neighbor',
                dckr_override=dckr_override
            )
            elapsed_ms = int((_time.monotonic() - t0) * 1000)

            if not output:
                print(f'rustbgpctl: empty output ({elapsed_ms}ms)')
                return None, None

            data = json.loads(output.decode('utf-8'))

            neighbors_received = {}
            neighbors_accepted = {}
            for neighbor in data:
                addr = neighbor.get('address', '')
                received = neighbor.get('prefixes_received', 0)
                neighbors_received[addr] = received
                neighbors_accepted[addr] = received

            if elapsed_ms > 5000:
                print(f'rustbgpctl: slow query ({elapsed_ms}ms)')

            return neighbors_received, neighbors_accepted

        except Exception as e:
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            print(f'rustbgpctl: error after {elapsed_ms}ms: {e}')
            return None, None
