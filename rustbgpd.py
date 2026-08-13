from base import *
from settings import dckr
import json
import os
import subprocess

# Default path to rustbgpd source tree for local builds
RUSTBGPD_SOURCE = os.environ.get('RUSTBGPD_SOURCE', '/home/lance/projects/rustbgpd')
RUSTBGPD_EVENT_HISTORY_ENV = 'RUSTBGPD_EVENT_HISTORY'
RUSTBGPD_EVENT_HISTORY_OFF_ENV = 'RUSTBGPD_EVENT_HISTORY_OFF'

RUST_BUILDER_IMAGE = (
    'rust:1.95-bookworm@sha256:'
    '6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1'
)
DEBIAN_RUNTIME_IMAGE = (
    'debian:bookworm-slim@sha256:'
    '60eac759739651111db372c07be67863818726f754804b8707c90979bda511df'
)

DOCKERFILE_CONTENT = '''\
FROM {builder_image} AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
RUN cargo build --workspace --release --locked
RUN mkdir -p /build-provenance \
    && {{ \
        echo 'builder_base={builder_image}'; \
        rustc --version --verbose; \
        cargo --version --verbose; \
        protoc --version; \
        dpkg-query -W -f='${{Package}}=${{Version}}\\n' | sort; \
    }} > /build-provenance/builder.txt

FROM {runtime_image}
WORKDIR /root

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release/rustbgpd /usr/local/bin/rustbgpd
COPY --from=builder /build/target/release/rbgp /usr/local/bin/rbgp
COPY --from=builder /build-provenance/builder.txt /usr/local/share/rustbgpd-builder-provenance.txt

RUN {{ \
        echo 'runtime_base={runtime_image}'; \
        dpkg-query -W -f='${{Package}}=${{Version}}\\n' | sort; \
    }} > /usr/local/share/rustbgpd-runtime-provenance.txt

RUN mkdir -p /var/lib/rustbgpd
'''.format(builder_image=RUST_BUILDER_IMAGE, runtime_image=DEBIAN_RUNTIME_IMAGE)

DOCKERFILE_CONTENT_PROF = '''\
FROM {builder_image} AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
RUN cargo build --workspace --profile release-prof --features jemalloc --locked
RUN mkdir -p /build-provenance \
    && {{ \
        echo 'builder_base={builder_image}'; \
        rustc --version --verbose; \
        cargo --version --verbose; \
        protoc --version; \
        dpkg-query -W -f='${{Package}}=${{Version}}\\n' | sort; \
    }} > /build-provenance/builder.txt

FROM {runtime_image}
WORKDIR /root

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release-prof/rustbgpd /usr/local/bin/rustbgpd
COPY --from=builder /build/target/release-prof/rbgp /usr/local/bin/rbgp
COPY --from=builder /build-provenance/builder.txt /usr/local/share/rustbgpd-builder-provenance.txt

RUN {{ \
        echo 'runtime_base={runtime_image}'; \
        dpkg-query -W -f='${{Package}}=${{Version}}\\n' | sort; \
    }} > /usr/local/share/rustbgpd-runtime-provenance.txt

RUN mkdir -p /var/lib/rustbgpd
'''.format(builder_image=RUST_BUILDER_IMAGE, runtime_image=DEBIAN_RUNTIME_IMAGE)

DOCKERFILE_CONTENT_DHAT = '''\
FROM {builder_image} AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .
RUN cargo build --workspace --profile release-prof --features dhat-heap --locked
RUN mkdir -p /build-provenance \
    && {{ \
        echo 'builder_base={builder_image}'; \
        rustc --version --verbose; \
        cargo --version --verbose; \
        protoc --version; \
        dpkg-query -W -f='${{Package}}=${{Version}}\\n' | sort; \
    }} > /build-provenance/builder.txt

FROM {runtime_image}
WORKDIR /root

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/release-prof/rustbgpd /usr/local/bin/rustbgpd
COPY --from=builder /build/target/release-prof/rbgp /usr/local/bin/rbgp
COPY --from=builder /build-provenance/builder.txt /usr/local/share/rustbgpd-builder-provenance.txt

RUN {{ \
        echo 'runtime_base={runtime_image}'; \
        dpkg-query -W -f='${{Package}}=${{Version}}\\n' | sort; \
    }} > /usr/local/share/rustbgpd-runtime-provenance.txt

RUN mkdir -p /var/lib/rustbgpd
'''.format(builder_image=RUST_BUILDER_IMAGE, runtime_image=DEBIAN_RUNTIME_IMAGE)


class RustBGPd(Container):
    CONTAINER_NAME = None
    GUEST_DIR = '/root/config'
    IMAGE_REPO = 'bgperf/rustbgpd'
    DEFAULT_REF = 'HEAD'
    SUPPORTS_VERSIONS = False
    DAEMON_BINARY = '/usr/local/bin/rustbgpd'

    def __init__(self, host_dir, conf, image='bgperf/rustbgpd'):
        super(RustBGPd, self).__init__(self.CONTAINER_NAME, image, host_dir, self.GUEST_DIR, conf)

    @classmethod
    def build_image(cls, force=False, tag='bgperf/rustbgpd', checkout='', nocache=False,
                    profile=False, version=None):
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

        source_revision = cls._clean_revision(source, 'rustbgpd source')
        cls._require_checkout_at_head(source, checkout, source_revision)
        adapter_root = os.path.dirname(os.path.realpath(__file__))
        cls._require_tracked_file(adapter_root, __file__, 'rustbgpd adapter')
        adapter_revision = cls._clean_revision(
            adapter_root,
            'bgperf2 adapter',
        )

        if profile == 'dhat':
            content = DOCKERFILE_CONTENT_DHAT
        elif profile:
            content = DOCKERFILE_CONTENT_PROF
        else:
            content = DOCKERFILE_CONTENT

        content = cls._render_dockerfile(
            content,
            source_revision,
            adapter_revision,
        )

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
                    raise RuntimeError(
                        line['errorDetail'].get('message', str(line['errorDetail']))
                    )

            image = dckr.inspect_image(tag)
            print(
                'built image identity: {} (rustbgpd {}, bgperf2 {})'.format(
                    image['Id'],
                    source_revision,
                    adapter_revision,
                )
            )
        finally:
            # Clean up the temp dockerfile
            if os.path.exists(dockerfile_path):
                os.remove(dockerfile_path)

    @classmethod
    def render_dockerfile(cls, version=None):
        """Render the release recipe without contacting Docker."""
        if version:
            cls.image_tag(version)
        source_revision = cls._clean_revision(RUSTBGPD_SOURCE, 'rustbgpd source')
        adapter_root = os.path.dirname(os.path.realpath(__file__))
        adapter_revision = cls._clean_revision(adapter_root, 'bgperf2 adapter')
        return cls._render_dockerfile(
            DOCKERFILE_CONTENT,
            source_revision,
            adapter_revision,
        )

    @staticmethod
    def _clean_revision(path, label):
        """Return a revision only for a clean Git worktree."""
        try:
            revision = subprocess.check_output(
                ['git', '-C', path, 'rev-parse', 'HEAD'],
                text=True,
            ).strip()
            status = subprocess.check_output(
                ['git', '-C', path, 'status', '--porcelain'],
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError('{} is not a readable Git checkout'.format(label)) from error

        if status:
            raise RuntimeError('{} must be clean before an image build'.format(label))
        return revision

    @staticmethod
    def _render_dockerfile(content, source_revision, adapter_revision):
        runtime_preamble = (
            'FROM {}\n'
            'LABEL org.opencontainers.image.revision="{}"\n'
            'LABEL org.rustbgpd.bgperf2.revision="{}"\n'
            'LABEL org.opencontainers.image.base.name="{}"\n'
            'LABEL org.opencontainers.image.base.digest="sha256:{}"\n'
            'LABEL org.rustbgpd.bgperf2.builder-base.digest="sha256:{}"\n'
            'LABEL org.rustbgpd.bgperf2.rust-toolchain="1.95"\n'
        ).format(
            DEBIAN_RUNTIME_IMAGE,
            source_revision,
            adapter_revision,
            DEBIAN_RUNTIME_IMAGE,
            DEBIAN_RUNTIME_IMAGE.rsplit('sha256:', 1)[1],
            RUST_BUILDER_IMAGE.rsplit('sha256:', 1)[1],
        )
        return content.replace(
            'FROM {}\n'.format(DEBIAN_RUNTIME_IMAGE),
            runtime_preamble,
            1,
        )

    @staticmethod
    def _require_checkout_at_head(path, checkout, head_revision):
        """Require the requested checkout to resolve to the clean source HEAD."""
        requested = checkout or 'HEAD'
        try:
            requested_revision = subprocess.check_output(
                ['git', '-C', path, 'rev-parse', '{}^{{commit}}'.format(requested)],
                text=True,
                stderr=subprocess.STDOUT,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError(
                'rustbgpd checkout {!r} does not resolve to a commit'.format(requested)
            ) from error

        if requested_revision != head_revision:
            raise RuntimeError(
                'rustbgpd checkout {!r} resolves to {}, but the clean source HEAD is {}; '
                'check out that revision in RUSTBGPD_SOURCE first'.format(
                    requested,
                    requested_revision,
                    head_revision,
                )
            )

    @staticmethod
    def _require_tracked_file(repo, path, label):
        relative_path = os.path.relpath(os.path.realpath(path), repo)
        try:
            subprocess.check_output(
                ['git', '-C', repo, 'ls-files', '--error-unmatch', relative_path],
                text=True,
                stderr=subprocess.STDOUT,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError('{} must be tracked by Git'.format(label)) from error


class RustBGPdTarget(RustBGPd, Target):

    CONTAINER_NAME = 'bgperf_rustbgpd_target'
    CONFIG_FILE_NAME = 'config.toml'

    def run(self, scenario_global_conf, dckr_net_name=''):
        self._require_policy_free_scenario(scenario_global_conf)
        return super(RustBGPdTarget, self).run(
            scenario_global_conf,
            dckr_net_name,
        )

    @staticmethod
    def _require_policy_free_scenario(scenario_global_conf):
        """Reject policy inputs this adapter cannot faithfully configure."""
        unsupported = []
        if scenario_global_conf.get('target', {}).get('filter_test'):
            unsupported.append('target.filter_test')
        if scenario_global_conf.get('policy'):
            unsupported.append('policy')

        for tester in scenario_global_conf.get('testers', []):
            if not tester:
                continue
            for neighbor_name, neighbor in tester.get('neighbors', {}).items():
                filters = neighbor.get('filter', {})
                if any(filters.values()):
                    unsupported.append(
                        'testers.neighbors.{}.filter'.format(neighbor_name)
                    )

        if unsupported:
            raise RuntimeError(
                'rustbgpd target does not implement generated filter/policy '
                'configuration: {}'.format(', '.join(unsupported))
            )

    @staticmethod
    def _event_history_mode(environ=None):
        """Resolve the explicit rustbgpd event-history benchmark mode."""
        environ = os.environ if environ is None else environ
        requested = environ.get(RUSTBGPD_EVENT_HISTORY_ENV)
        legacy_off = environ.get(RUSTBGPD_EVENT_HISTORY_OFF_ENV)

        if requested is None:
            # Keep the old OFF switch's non-empty-string truthiness. When no
            # mode is selected, omit the block so the adapter can still run
            # against rustbgpd revisions that predate [event_history].
            return 'disabled' if legacy_off else None

        if requested not in ('enabled', 'disabled'):
            raise RuntimeError(
                '{} must be "enabled" or "disabled", got {!r}'.format(
                    RUSTBGPD_EVENT_HISTORY_ENV,
                    requested,
                )
            )
        if legacy_off and requested == 'enabled':
            raise RuntimeError(
                '{}=enabled conflicts with legacy {}'.format(
                    RUSTBGPD_EVENT_HISTORY_ENV,
                    RUSTBGPD_EVENT_HISTORY_OFF_ENV,
                )
            )
        return requested

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
        # The durable event-history outbox (ADR-0072) is opt-in/default-off on
        # current rustbgpd. Render explicit selections, while leaving the block
        # absent when unset so pre-v0.30 daemon revisions remain benchmarkable.
        event_history_mode = self._event_history_mode()
        if event_history_mode is not None:
            config += '[event_history]\n'
            config += 'enabled = {}\n'.format(
                'true' if event_history_mode == 'enabled' else 'false'
            )
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
        """Query neighbor state over rustbgpd's default owner-only Unix socket.

        Returns (neighbors_received, neighbors_accepted) dicts keyed by
        neighbor address.  On any failure (timeout, parse error, empty
        output) returns (None, None) so callers can distinguish "query
        failed" from "zero neighbors."
        """
        import time as _time
        t0 = _time.monotonic()
        try:
            output = self.local(
                'rbgp --json neighbor',
                dckr_override=dckr_override
            )
            elapsed_ms = int((_time.monotonic() - t0) * 1000)

            if not output:
                print(f'rbgp: empty output ({elapsed_ms}ms)')
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
                print(f'rbgp: slow query ({elapsed_ms}ms)')

            return neighbors_received, neighbors_accepted

        except Exception as e:
            elapsed_ms = int((_time.monotonic() - t0) * 1000)
            print(f'rbgp: error after {elapsed_ms}ms: {e}')
            return None, None
