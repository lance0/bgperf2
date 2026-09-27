'''rustbgpd target built from an exact local source revision.'''

import json
import os
import subprocess

from base import *
from settings import dckr


RUSTBGPD_SOURCE = os.environ.get(
    'RUSTBGPD_SOURCE', '/home/lance/projects/rustbgpd')
RUSTBGPD_EVENT_HISTORY_ENV = 'RUSTBGPD_EVENT_HISTORY'
RUSTBGPD_EVENT_HISTORY_OFF_ENV = 'RUSTBGPD_EVENT_HISTORY_OFF'

# Both bases are pinned by multi-platform OCI index digest. The rustbgpd
# workspace declares Rust 1.95 as its MSRV; Bookworm on both stages also keeps
# the built binary and runtime libc aligned.
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


class RustBGPd(Container):
    CONTAINER_NAME = None
    GUEST_DIR = '/root/config'
    IMAGE_REPO = 'bgperf/rustbgpd'
    DEFAULT_REF = 'HEAD'
    SUPPORTS_VERSIONS = False
    DAEMON_BINARY = '/usr/local/bin/rustbgpd'

    def __init__(self, host_dir, conf, image='bgperf/rustbgpd'):
        super(RustBGPd, self).__init__(
            self.CONTAINER_NAME, image, host_dir, self.GUEST_DIR, conf)

    @classmethod
    def build_image(cls, force=False, tag=None, checkout=None, nocache=False,
                    version=None):
        '''Build from a clean local rustbgpd worktree without mutating it.'''
        tag = tag or cls.image_tag()
        source = RUSTBGPD_SOURCE
        if not os.path.isdir(source):
            raise RuntimeError(
                'rustbgpd source not found at {}'.format(source))
        if not force and img_exists(tag):
            return

        source_revision = cls._clean_revision(source, 'rustbgpd source')
        cls._require_checkout_at_head(source, checkout, source_revision)

        adapter_root = os.path.dirname(os.path.realpath(__file__))
        cls._require_tracked_file(adapter_root, __file__, 'rustbgpd adapter')
        adapter_revision = cls._clean_revision(adapter_root, 'bgperf2 adapter')
        content = cls._render_dockerfile(
            DOCKERFILE_CONTENT, source_revision, adapter_revision)

        dockerfile_path = os.path.join(source, 'Dockerfile.bgperf')
        try:
            with open(dockerfile_path, 'w') as dockerfile:
                dockerfile.write(content)

            print('build {0} from clean source {1} at {2}'.format(
                tag, source, source_revision))
            for line in dckr.build(
                    path=source, dockerfile='Dockerfile.bgperf', rm=True,
                    tag=tag, decode=True, nocache=nocache):
                if 'stream' in line:
                    print(line['stream'].strip())
                if 'errorDetail' in line:
                    detail = line['errorDetail']
                    raise ImageBuildFailed(
                        tag, detail.get('message') or str(detail))

            image = dckr.inspect_image(tag)
            print('built image identity: {0} (rustbgpd {1}, bgperf2 {2})'.format(
                image['Id'], source_revision, adapter_revision))
        finally:
            if os.path.exists(dockerfile_path):
                os.remove(dockerfile_path)

    @classmethod
    def render_dockerfile(cls, version=None):
        '''Render the local-source recipe without contacting Docker.'''
        if version:
            cls.image_tag(version)
        source_revision = cls._clean_revision(
            RUSTBGPD_SOURCE, 'rustbgpd source')
        adapter_root = os.path.dirname(os.path.realpath(__file__))
        adapter_revision = cls._clean_revision(adapter_root, 'bgperf2 adapter')
        return cls._render_dockerfile(
            DOCKERFILE_CONTENT, source_revision, adapter_revision)

    @staticmethod
    def _clean_revision(path, label):
        '''Return HEAD only when the named Git worktree is clean.'''
        try:
            revision = subprocess.check_output(
                ['git', '-C', path, 'rev-parse', 'HEAD'], text=True).strip()
            status = subprocess.check_output(
                ['git', '-C', path, 'status', '--porcelain'], text=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError(
                '{} is not a readable Git checkout'.format(label)) from error
        if status:
            raise RuntimeError(
                '{} must be clean before an image build'.format(label))
        return revision

    @staticmethod
    def _require_checkout_at_head(path, checkout, head_revision):
        '''Treat --checkout as an assertion, never as permission to mutate.'''
        requested = checkout or 'HEAD'
        try:
            requested_revision = subprocess.check_output(
                ['git', '-C', path, 'rev-parse',
                 '{}^{{commit}}'.format(requested)],
                text=True, stderr=subprocess.STDOUT).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError(
                'rustbgpd checkout {!r} does not resolve to a commit'.format(
                    requested)) from error
        if requested_revision != head_revision:
            raise RuntimeError(
                'rustbgpd checkout {!r} resolves to {}, but the clean source '
                'HEAD is {}; check out that revision in RUSTBGPD_SOURCE first'.format(
                    requested, requested_revision, head_revision))

    @staticmethod
    def _require_tracked_file(repo, path, label):
        relative_path = os.path.relpath(os.path.realpath(path), repo)
        try:
            subprocess.check_output(
                ['git', '-C', repo, 'ls-files', '--error-unmatch', relative_path],
                text=True, stderr=subprocess.STDOUT)
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError('{} must be tracked by Git'.format(label)) from error

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
            'FROM {}\n'.format(DEBIAN_RUNTIME_IMAGE), runtime_preamble, 1)

    def get_version_cmd(self):
        return 'rustbgpd --version'

    def exec_version_cmd(self):
        reported = (super(RustBGPd, self).exec_version_cmd() or '').strip()
        if not reported.startswith('rustbgpd '):
            raise VersionUnavailable(
                'unexpected output from `{0}`: {1!r}'.format(
                    self.get_version_cmd(), reported))
        return reported


class RustBGPdTarget(RustBGPd, Target):
    CONTAINER_NAME = 'bgperf_rustbgpd_target'
    CONFIG_FILE_NAME = 'config.toml'

    @staticmethod
    def _event_history_mode(environ=None):
        '''Resolve an explicit event-history mode across daemon generations.'''
        environ = os.environ if environ is None else environ
        requested = environ.get(RUSTBGPD_EVENT_HISTORY_ENV)
        legacy_off = environ.get(RUSTBGPD_EVENT_HISTORY_OFF_ENV)

        if requested is None:
            return 'disabled' if legacy_off else None
        if requested not in ('enabled', 'disabled'):
            raise RuntimeError(
                '{} must be "enabled" or "disabled", got {!r}'.format(
                    RUSTBGPD_EVENT_HISTORY_ENV, requested))
        if legacy_off and requested == 'enabled':
            raise RuntimeError(
                '{}=enabled conflicts with legacy {}'.format(
                    RUSTBGPD_EVENT_HISTORY_ENV,
                    RUSTBGPD_EVENT_HISTORY_OFF_ENV))
        return requested

    def reject_unsupported_policy(self):
        '''Reject inputs this adapter cannot represent before container start.'''
        unsupported = []
        if self.conf.get('filter_test'):
            unsupported.append('target.filter_test')
        if self.scenario_global_conf.get('policy'):
            unsupported.append('policy')
        for tester in self.scenario_global_conf.get('testers') or []:
            for name, neighbor in (tester or {}).get('neighbors', {}).items():
                if any((neighbor.get('filter') or {}).values()):
                    unsupported.append(
                        'testers.neighbors.{}.filter'.format(name))
        if unsupported:
            raise NotImplementedError(
                'the rustbgpd target does not generate policy configuration, '
                'so it would ignore: {}'.format(
                    ', '.join(sorted(set(unsupported)))))

    def write_config(self):
        self.reject_unsupported_policy()

        lines = [
            '[global]',
            'asn = {}'.format(self.conf['as']),
            'router_id = "{}"'.format(self.conf['router-id']),
            'listen_port = 179',
            '',
            '[global.telemetry]',
            'prometheus_addr = "0.0.0.0:9179"',
            'log_format = "json"',
            '',
        ]
        event_history_mode = self._event_history_mode()
        if event_history_mode is not None:
            lines.extend([
                '[event_history]',
                'enabled = {}'.format(
                    'true' if event_history_mode == 'enabled' else 'false'),
                '',
            ])

        # Unsorted keeps the historical tester-then-monitor order, and the
        # shared seam adds any export-fan-out receivers.
        for neighbor in self.scenario_neighbors(sort=False):
            lines.extend([
                '[[neighbors]]',
                'address = "{}"'.format(neighbor['local-address']),
                'remote_asn = {}'.format(neighbor['as']),
                '',
            ])

        with open(os.path.join(self.host_dir, self.CONFIG_FILE_NAME), 'w') as config:
            config.write('\n'.join(lines))

    def get_startup_cmd(self):
        return '\n'.join([
            '#!/bin/bash',
            'ulimit -n 65536',
            'exec rustbgpd {guest_dir}/{config_file_name} '
            '> {guest_dir}/rustbgpd.log 2>&1',
        ]).format(
            guest_dir=self.guest_dir,
            config_file_name=self.CONFIG_FILE_NAME)

    def get_neighbors_state(self):
        """Return received counts from rbgp, or empty state before it answers.

        Empty stdout is the daemon's socket not being up yet, a poll or two
        after start, and reads as no neighbours. Anything else that is not the
        expected JSON raises, so the shared sampler counts it as a failed read
        and the run's event artifact reports it instead of a silent gap.
        """
        output = self.local('rbgp --json neighbor')
        if not output:
            return {}, {}
        neighbors = json.loads(output.decode('utf-8'))
        if not isinstance(neighbors, list):
            raise ValueError(
                'rbgp neighbor output is not a list: {!r}'.format(neighbors))
        received = {}
        for neighbor in neighbors:
            if not isinstance(neighbor, dict):
                raise ValueError(
                    'rbgp neighbor entry is not an object: {!r}'.format(neighbor))
            address = neighbor.get('address')
            if not address:
                continue
            received[address] = int(neighbor.get('prefixes_received', 0))
        return received, dict(received)
