'''The local-source rustbgpd adapter, without Docker or a live daemon.'''

import subprocess

import pytest

import bgperf2
import rustbgpd
from base import VersionUnavailable
from rustbgpd import (
    DEBIAN_RUNTIME_IMAGE,
    DOCKERFILE_CONTENT,
    RUST_BUILDER_IMAGE,
    RUSTBGPD_EVENT_HISTORY_ENV,
    RUSTBGPD_EVENT_HISTORY_OFF_ENV,
    RustBGPd,
    RustBGPdTarget,
)


@pytest.fixture(autouse=True)
def no_ambient_event_history_switch(monkeypatch):
    monkeypatch.delenv(RUSTBGPD_EVENT_HISTORY_ENV, raising=False)
    monkeypatch.delenv(RUSTBGPD_EVENT_HISTORY_OFF_ENV, raising=False)


SCENARIO = {
    'testers': [{'neighbors': {
        '10.10.0.3': {
            'as': 1003,
            'local-address': '10.10.0.3',
            'filter': {'in': []},
        },
        '10.10.0.4': {
            'as': 1004,
            'local-address': '10.10.0.4',
            'filter': {'in': []},
        },
    }}],
    'monitor': {'as': 1001, 'local-address': '10.10.0.2'},
    'policy': {},
}


def write(tmp_path, scenario=None, conf=None):
    target = object.__new__(RustBGPdTarget)
    target.host_dir = str(tmp_path)
    target.conf = conf if conf is not None else {
        'as': 1000,
        'router-id': '10.10.255.254',
    }
    target.scenario_global_conf = SCENARIO if scenario is None else scenario
    target.write_config()
    return (tmp_path / RustBGPdTarget.CONFIG_FILE_NAME).read_text()


class TestRegistration:
    def test_upstream_image_and_target_registries_include_rustbgpd(self):
        assert bgperf2.BUILDABLE_IMAGES['rustbgpd'] is RustBGPd
        assert bgperf2.TARGET_CLASSES['rustbgpd'] is RustBGPdTarget
        # A bare prepare remains portable for users without a local rustbgpd
        # checkout. Explicit `prepare -t rustbgpd` and `update rustbgpd` use
        # BUILDABLE_IMAGES and remain available.
        assert 'rustbgpd' not in bgperf2.PREPARE_IMAGES
        assert RustBGPd.IMAGE_REPO == 'bgperf/rustbgpd'
        assert RustBGPd.SUPPORTS_VERSIONS is False


class TestBuildRecipe:
    def test_build_is_locked_and_bases_are_digest_pinned(self):
        assert '@sha256:' in RUST_BUILDER_IMAGE
        assert '@sha256:' in DEBIAN_RUNTIME_IMAGE
        assert 'FROM {} AS builder'.format(RUST_BUILDER_IMAGE) in DOCKERFILE_CONTENT
        assert 'FROM {}'.format(DEBIAN_RUNTIME_IMAGE) in DOCKERFILE_CONTENT
        assert 'cargo build --workspace --release --locked' in DOCKERFILE_CONTENT
        assert '/target/release/rustbgpd /usr/local/bin/rustbgpd' in DOCKERFILE_CONTENT
        assert '/target/release/rbgp /usr/local/bin/rbgp' in DOCKERFILE_CONTENT
        assert 'rustbgpd-builder-provenance.txt' in DOCKERFILE_CONTENT
        assert 'rustbgpd-runtime-provenance.txt' in DOCKERFILE_CONTENT

    def test_rendered_recipe_labels_both_revisions_and_bases(self):
        rendered = RustBGPd._render_dockerfile(
            DOCKERFILE_CONTENT, 'a' * 40, 'b' * 40)
        assert 'org.opencontainers.image.revision="{}"'.format('a' * 40) in rendered
        assert 'org.rustbgpd.bgperf2.revision="{}"'.format('b' * 40) in rendered
        assert 'org.opencontainers.image.base.digest="sha256:{}"'.format(
            DEBIAN_RUNTIME_IMAGE.rsplit('sha256:', 1)[1]) in rendered
        assert 'org.rustbgpd.bgperf2.builder-base.digest="sha256:{}"'.format(
            RUST_BUILDER_IMAGE.rsplit('sha256:', 1)[1]) in rendered

    def test_render_dockerfile_does_not_contact_docker(self, monkeypatch):
        revisions = iter(('a' * 40, 'b' * 40))
        monkeypatch.setattr(
            RustBGPd, '_clean_revision',
            lambda *args: next(revisions))
        # The module-level client exists at import time, but rendering must not
        # invoke it. A sentinel object makes any accidental method call fail.
        monkeypatch.setattr('rustbgpd.dckr', object())
        rendered = RustBGPd.render_dockerfile()
        assert 'org.opencontainers.image.revision="{}"'.format('a' * 40) in rendered

    def test_clean_revision_rejects_dirty_checkout(self, monkeypatch):
        replies = iter(('abc123\n', ' M rustbgpd.py\n'))
        monkeypatch.setattr(
            subprocess, 'check_output', lambda *args, **kwargs: next(replies))
        with pytest.raises(RuntimeError, match='must be clean'):
            RustBGPd._clean_revision('/repo', 'adapter')

    def test_checkout_is_an_assertion_against_clean_head(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, 'check_output', lambda *args, **kwargs: 'abc123\n')
        RustBGPd._require_checkout_at_head('/repo', 'release', 'abc123')

        monkeypatch.setattr(
            subprocess, 'check_output', lambda *args, **kwargs: 'def456\n')
        with pytest.raises(RuntimeError, match='clean source HEAD is abc123'):
            RustBGPd._require_checkout_at_head('/repo', 'release', 'abc123')

    def test_adapter_file_must_be_tracked(self, monkeypatch):
        def fail(*args, **kwargs):
            raise subprocess.CalledProcessError(1, 'git')

        monkeypatch.setattr(subprocess, 'check_output', fail)
        with pytest.raises(RuntimeError, match='must be tracked'):
            RustBGPd._require_tracked_file(
                '/repo', '/repo/rustbgpd.py', 'adapter')

    def test_build_uses_local_context_and_removes_temporary_recipe(
            self, tmp_path, monkeypatch):
        calls = {}

        class FakeDocker:
            def build(self, **kwargs):
                calls['build'] = kwargs
                assert (tmp_path / 'Dockerfile.bgperf').exists()
                yield {'stream': 'ok'}

            def inspect_image(self, tag):
                return {'Id': 'sha256:built'}

        monkeypatch.setattr(rustbgpd, 'RUSTBGPD_SOURCE', str(tmp_path))
        monkeypatch.setattr(rustbgpd, 'dckr', FakeDocker())
        monkeypatch.setattr(rustbgpd, 'img_exists', lambda tag: False)
        monkeypatch.setattr(
            RustBGPd, '_clean_revision',
            lambda path, label: 'a' * 40 if path == str(tmp_path) else 'b' * 40)
        monkeypatch.setattr(
            RustBGPd, '_require_checkout_at_head', lambda *args: None)
        monkeypatch.setattr(RustBGPd, '_require_tracked_file', lambda *args: None)

        RustBGPd.build_image(
            force=True, tag='bgperf/rustbgpd:test', checkout='a' * 40)

        assert calls['build']['path'] == str(tmp_path)
        assert calls['build']['dockerfile'] == 'Dockerfile.bgperf'
        assert calls['build']['tag'] == 'bgperf/rustbgpd:test'
        assert not (tmp_path / 'Dockerfile.bgperf').exists()


class TestWriteConfig:
    def test_every_tester_neighbor_and_monitor_is_configured(self, tmp_path):
        config = write(tmp_path)
        for address in ('10.10.0.3', '10.10.0.4', '10.10.0.2'):
            assert 'address = "{}"'.format(address) in config
        assert config.count('[[neighbors]]') == 3

    def test_export_receivers_are_configured_after_the_monitor(self, tmp_path):
        scenario = dict(SCENARIO, receivers=[
            {'as': 1101, 'local-address': '10.10.0.101'},
            {'as': 1102, 'local-address': '10.10.0.102'},
        ])
        config = write(tmp_path, scenario=scenario)
        order = [line.split('"')[1] for line in config.splitlines()
                 if line.startswith('address = ')]
        assert order == ['10.10.0.3', '10.10.0.4', '10.10.0.2',
                         '10.10.0.101', '10.10.0.102']

    def test_owner_only_socket_needs_no_grpc_security_block(self, tmp_path):
        config = write(tmp_path)
        assert 'security.grpc' not in config
        assert 'grpc_tcp' not in config

    def test_unset_event_history_omits_block(self, tmp_path):
        assert '[event_history]' not in write(tmp_path)

    @pytest.mark.parametrize('mode,expected', [
        ('enabled', 'true'),
        ('disabled', 'false'),
    ])
    def test_event_history_mode_is_explicit(self, tmp_path, monkeypatch,
                                            mode, expected):
        monkeypatch.setenv(RUSTBGPD_EVENT_HISTORY_ENV, mode)
        assert '[event_history]\nenabled = {}'.format(expected) in write(tmp_path)

    @pytest.mark.parametrize('mode', ['', 'yes', 'ENABLED', ' enabled '])
    def test_event_history_requires_exact_tokens(self, tmp_path, monkeypatch,
                                                 mode):
        monkeypatch.setenv(RUSTBGPD_EVENT_HISTORY_ENV, mode)
        with pytest.raises(RuntimeError, match='must be "enabled" or "disabled"'):
            write(tmp_path)

    def test_legacy_off_keeps_nonempty_truthiness(self, tmp_path, monkeypatch):
        monkeypatch.setenv(RUSTBGPD_EVENT_HISTORY_OFF_ENV, '0')
        assert '[event_history]\nenabled = false' in write(tmp_path)

    def test_empty_legacy_off_is_inactive(self, tmp_path, monkeypatch):
        monkeypatch.setenv(RUSTBGPD_EVENT_HISTORY_OFF_ENV, '')
        assert '[event_history]' not in write(tmp_path)

    def test_enabled_conflicts_with_legacy_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv(RUSTBGPD_EVENT_HISTORY_ENV, 'enabled')
        monkeypatch.setenv(RUSTBGPD_EVENT_HISTORY_OFF_ENV, '1')
        with pytest.raises(RuntimeError, match='conflicts with legacy'):
            write(tmp_path)


class TestUnsupportedPolicy:
    def test_filter_test_is_refused(self, tmp_path):
        conf = {
            'as': 1000,
            'router-id': '10.10.255.254',
            'filter_test': 'transit',
        }
        with pytest.raises(NotImplementedError, match='target.filter_test'):
            write(tmp_path, conf=conf)

    def test_generated_policy_is_refused(self, tmp_path):
        scenario = dict(SCENARIO, policy={'p1': {'match': []}})
        with pytest.raises(NotImplementedError, match='policy'):
            write(tmp_path, scenario=scenario)

    def test_neighbor_filter_is_refused(self, tmp_path):
        scenario = dict(SCENARIO, testers=[{'neighbors': {
            '10.10.0.3': {
                'as': 1003,
                'local-address': '10.10.0.3',
                'filter': {'in': ['p1']},
            },
        }}])
        with pytest.raises(NotImplementedError, match='10.10.0.3'):
            write(tmp_path, scenario=scenario)

    def test_empty_filter_assignment_is_allowed(self, tmp_path):
        assert write(tmp_path)


class TestRbgpParsing:
    def target_with_output(self, output):
        target = object.__new__(RustBGPdTarget)
        if isinstance(output, Exception):
            def local(_command):
                raise output
            target.local = local
        else:
            target.local = lambda _command: output
        return target

    def test_maps_received_prefixes_to_both_contract_dicts(self):
        target = self.target_with_output(
            b'[{"address":"10.0.0.1","prefixes_received":100000},'
            b'{"address":"10.0.0.2","prefixes_received":99999}]')
        expected = {'10.0.0.1': 100000, '10.0.0.2': 99999}
        assert target.get_neighbors_state() == (expected, expected)

    def test_empty_output_before_the_socket_is_up_is_empty_state(self):
        target = self.target_with_output(b'')
        assert target.get_neighbors_state() == ({}, {})

    @pytest.mark.parametrize('output', [
        b'not json',
        b'{}',
        b'[1]',
        b'[{"address":"10.0.0.1","prefixes_received":null}]',
    ])
    def test_malformed_output_is_a_failed_read(self, output):
        target = self.target_with_output(output)
        with pytest.raises((ValueError, TypeError)):
            target.get_neighbors_state()

    def test_exec_failure_is_a_failed_read(self):
        target = self.target_with_output(RuntimeError('exec failed'))
        with pytest.raises(RuntimeError):
            target.get_neighbors_state()


class TestVersion:
    def test_daemon_banner_is_accepted(self, monkeypatch):
        monkeypatch.setattr(
            'base.Container.exec_version_cmd',
            lambda self, stderr=False: 'rustbgpd 0.67.0\n')
        assert object.__new__(RustBGPd).exec_version_cmd() == 'rustbgpd 0.67.0'

    @pytest.mark.parametrize('output', [
        '',
        'OCI runtime exec failed: executable file not found',
        'rbgp 0.67.0',
    ])
    def test_other_output_is_not_a_version(self, monkeypatch, output):
        monkeypatch.setattr(
            'base.Container.exec_version_cmd',
            lambda self, stderr=False: output)
        with pytest.raises(VersionUnavailable):
            object.__new__(RustBGPd).exec_version_cmd()
