import argparse
import datetime
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import base as base_module
import bgperf2
from flock import FlockTarget
from frr import FRRoutingTarget
from openbgp import OpenBGPTarget
from rustbgpd import (
    DEBIAN_RUNTIME_IMAGE,
    DOCKERFILE_CONTENT,
    DOCKERFILE_CONTENT_DHAT,
    DOCKERFILE_CONTENT_PROF,
    RUST_BUILDER_IMAGE,
    RUSTBGPD_EVENT_HISTORY_ENV,
    RUSTBGPD_EVENT_HISTORY_OFF_ENV,
    RustBGPd,
    RustBGPdTarget,
)
from srlinux import SRLinuxTarget


class RustBgpdAdapterTests(unittest.TestCase):
    def render_rustbgpd_config(self, environ):
        target = object.__new__(RustBGPdTarget)
        target.conf = {
            'as': 65000,
            'router-id': '192.0.2.1',
        }
        target.scenario_global_conf = {
            'testers': [],
            'monitor': {
                'local-address': '192.0.2.2',
                'as': 65001,
            },
        }
        with tempfile.TemporaryDirectory() as host_dir:
            target.host_dir = host_dir
            with mock.patch.dict(os.environ, environ, clear=True):
                target.write_config()
            with open(os.path.join(host_dir, target.CONFIG_FILE_NAME)) as config_file:
                return config_file.read()

    def test_unset_event_history_omits_block_for_old_revision_compatibility(self):
        config = self.render_rustbgpd_config({})

        self.assertNotIn('[event_history]', config)

    def test_event_history_can_be_selected_explicitly(self):
        enabled = self.render_rustbgpd_config({
            RUSTBGPD_EVENT_HISTORY_ENV: 'enabled',
        })
        disabled = self.render_rustbgpd_config({
            RUSTBGPD_EVENT_HISTORY_ENV: 'disabled',
        })

        self.assertIn('[event_history]\nenabled = true\n', enabled)
        self.assertIn('[event_history]\nenabled = false\n', disabled)

    def test_event_history_mode_requires_exact_tokens(self):
        for value in ('yes', ' Enabled ', 'ENABLED', 'Disabled', ''):
            with self.subTest(value=value), self.assertRaisesRegex(
                    RuntimeError, 'must be "enabled" or "disabled"'):
                RustBGPdTarget._event_history_mode({
                    RUSTBGPD_EVENT_HISTORY_ENV: value,
                })

    def test_event_history_enabled_conflicts_with_truthy_legacy_off(self):
        with self.assertRaisesRegex(RuntimeError, 'conflicts with legacy'):
            RustBGPdTarget._event_history_mode({
                RUSTBGPD_EVENT_HISTORY_ENV: 'enabled',
                RUSTBGPD_EVENT_HISTORY_OFF_ENV: '0',
            })

    def test_legacy_event_history_off_switch_remains_disabled(self):
        config = self.render_rustbgpd_config({
            RUSTBGPD_EVENT_HISTORY_OFF_ENV: '0',
        })

        self.assertIn('[event_history]\nenabled = false\n', config)

    def test_empty_legacy_event_history_off_switch_remains_inactive(self):
        unset = self.render_rustbgpd_config({
            RUSTBGPD_EVENT_HISTORY_OFF_ENV: '',
        })
        enabled = self.render_rustbgpd_config({
            RUSTBGPD_EVENT_HISTORY_ENV: 'enabled',
            RUSTBGPD_EVENT_HISTORY_OFF_ENV: '',
        })

        self.assertNotIn('[event_history]', unset)
        self.assertIn('[event_history]\nenabled = true\n', enabled)

    def test_openbgp_filter_file_is_closed(self):
        opened = mock.mock_open(read_data='filter contents')
        target = object.__new__(OpenBGPTarget)

        with mock.patch('builtins.open', opened):
            self.assertEqual(target.get_filter_test_config(), 'filter contents')

        opened.assert_called_once_with('filters/openbgp.conf', mode='r')
        opened.return_value.__enter__.assert_called_once_with()
        opened.return_value.__exit__.assert_called_once()

    def test_neighbor_stats_closes_dedicated_docker_client(self):
        class ImmediateThread:
            def __init__(self, target):
                self.target = target
                self.daemon = False

            def start(self):
                self.target()

        container = object.__new__(base_module.Container)
        container.stop_monitoring = True
        client = mock.Mock()

        with mock.patch('base.Thread', ImmediateThread), \
                mock.patch('settings.Client', return_value=client) as client_factory:
            container.neighbor_stats(mock.Mock())

        client_factory.assert_called_once_with(version='auto')
        client.close.assert_called_once_with()

    def test_all_build_profiles_use_root_lockfile(self):
        for dockerfile in (
            DOCKERFILE_CONTENT,
            DOCKERFILE_CONTENT_PROF,
            DOCKERFILE_CONTENT_DHAT,
        ):
            self.assertIn('cargo build --workspace', dockerfile)
            self.assertIn('--locked', dockerfile)
            self.assertIn('FROM {} AS builder'.format(RUST_BUILDER_IMAGE), dockerfile)
            self.assertIn('FROM {}'.format(DEBIAN_RUNTIME_IMAGE), dockerfile)
            self.assertIn('rustbgpd-builder-provenance.txt', dockerfile)
            self.assertIn('rustbgpd-runtime-provenance.txt', dockerfile)

        rendered = RustBGPd._render_dockerfile(
            DOCKERFILE_CONTENT_DHAT,
            'a' * 40,
            'b' * 40,
        )
        self.assertIn(
            'LABEL org.opencontainers.image.revision="{}"\n'.format('a' * 40),
            rendered,
        )
        self.assertIn(
            'LABEL org.rustbgpd.bgperf2.revision="{}"\n'.format('b' * 40),
            rendered,
        )
        self.assertIn(
            'LABEL org.opencontainers.image.base.digest="sha256:{}"'.format(
                DEBIAN_RUNTIME_IMAGE.rsplit('sha256:', 1)[1]
            ),
            rendered,
        )

    def test_monitor_fallback_is_scoped_to_rustbgpd(self):
        required = 200000
        for target in ('bird', 'flock', 'frr', 'srlinux'):
            checkpoint = False
            for received in (0, required, required + 1):
                checkpoint = bgperf2.monitor_confirms_neighbor_checkpoint(
                    target,
                    received,
                    required,
                    checkpoint,
                )
                self.assertFalse(checkpoint, target)

        checkpoint = False
        for received in (0, required):
            checkpoint = bgperf2.monitor_confirms_neighbor_checkpoint(
                'rustbgpd',
                received,
                required,
                checkpoint,
            )
        self.assertTrue(checkpoint)
        self.assertTrue(
            bgperf2.monitor_confirms_neighbor_checkpoint('frr', 0, required, True)
        )

    def test_zero_route_failure_grace_is_scoped_to_rustbgpd(self):
        self.assertEqual(bgperf2.zero_route_failure_grace_seconds('rustbgpd'), 120)
        for target in ('bird', 'flock', 'frr', 'gobgp', 'srlinux'):
            self.assertEqual(
                bgperf2.zero_route_failure_grace_seconds(target),
                15,
                target,
            )

    def test_rustbgpd_rejects_unimplemented_policy_inputs(self):
        clean = {
            'target': {},
            'policy': {},
            'testers': [{
                'neighbors': {
                    '10.0.0.1': {'filter': {'in': []}},
                },
            }],
        }
        RustBGPdTarget._require_policy_free_scenario(clean)

        scenarios = (
            {
                'target': {'filter_test': 'ixp'},
                'policy': {},
                'testers': [],
            },
            {
                'target': {},
                'policy': {'p1': {'match': []}},
                'testers': [],
            },
            {
                'target': {},
                'policy': {},
                'testers': [{
                    'neighbors': {
                        '10.0.0.1': {'filter': {'out': ['p1']}},
                    },
                }],
            },
        )
        for scenario in scenarios:
            with self.assertRaisesRegex(RuntimeError, 'does not implement'):
                RustBGPdTarget._require_policy_free_scenario(scenario)

    def test_neighbor_stats_override_compatibility(self):
        scenario = {
            'testers': [{
                'neighbors': {
                    '10.0.0.1': {'check-points': 10},
                },
            }],
        }

        frr = object.__new__(FRRoutingTarget)
        frr.scenario_global_conf = scenario
        frr.get_neighbors_state = mock.Mock(return_value=({}, {'10.0.0.1': 10}))
        frr._get_EOR_from_log = mock.Mock(return_value={'10.0.0.1': True})
        self.assertEqual(
            frr.get_neighbor_received_routes(dckr_override='client'),
            ({'10.0.0.1': True}, {'10.0.0.1': True}),
        )
        frr.get_neighbors_state.assert_called_once_with(dckr_override='client')

        for target_class in (FlockTarget, SRLinuxTarget):
            target = object.__new__(target_class)
            target.scenario_global_conf = scenario
            target.get_neighbors_state = mock.Mock(return_value=2)
            self.assertEqual(
                target.get_neighbor_received_routes(dckr_override='client'),
                ({'10.0.0.1': True}, {'10.0.0.1': True}),
            )
            target.get_neighbors_state.assert_called_once_with(dckr_override='client')

    def test_neighbor_json_maps_received_prefixes(self):
        target = object.__new__(RustBGPdTarget)
        target.local = mock.Mock(
            return_value=(
                b'[{"address":"10.0.0.1","prefixes_received":100000},'
                b'{"address":"10.0.0.2","prefixes_received":99999}]'
            )
        )

        received, accepted = target.get_neighbors_state(dckr_override='client')

        expected = {'10.0.0.1': 100000, '10.0.0.2': 99999}
        self.assertEqual(received, expected)
        self.assertEqual(accepted, expected)
        target.local.assert_called_once_with(
            'rustbgpctl -s http://127.0.0.1:50051 --json neighbor',
            dckr_override='client',
        )

    def test_exact_receipt_shape_does_not_use_99_percent_checkpoint(self):
        args = argparse.Namespace(
            neighbor_num=2,
            prefix_num=100000,
            as_path_list_num=0,
            prefix_list_num=0,
            community_list_num=0,
            ext_community_list_num=0,
            tester_type='bird',
            local_address_prefix='10.10.0.0/16',
            target_local_address=None,
            monitor_local_address=None,
            target_router_id=None,
            monitor_router_id=None,
            single_table=False,
            license_file=None,
            target_config_file=None,
            filter_test=None,
            filter_type='in',
            target='rustbgpd',
        )

        rendered = bgperf2.gen_conf(args)
        conf = bgperf2.yaml.safe_load(rendered.split('%>\n', 1)[1])

        self.assertEqual(conf['monitor']['check-points'], [200000])

    def test_update_cli_exposes_revision_pinned_dhat_build(self):
        parser = bgperf2.create_args_parser()
        args = parser.parse_args([
            'update',
            'rustbgpd',
            '--profile',
            'dhat',
            '--tag',
            'bgperf/rustbgpd-receipt',
            '--no-cache',
        ])

        self.assertEqual(args.image, 'rustbgpd')
        self.assertEqual(args.profile, 'dhat')
        self.assertEqual(args.tag, 'bgperf/rustbgpd-receipt')
        self.assertTrue(args.no_cache)
        with mock.patch.object(bgperf2.RustBGPd, 'build_image') as build_image:
            bgperf2.update(args)
        build_image.assert_called_once_with(
            True,
            tag='bgperf/rustbgpd-receipt',
            checkout='HEAD',
            nocache=True,
            profile='dhat',
        )

    def test_csv_quirk_uses_distinct_tester_counter_sentinels(self):
        args = argparse.Namespace(
            label=None,
            target='rustbgpd',
            neighbor_num=2,
            prefix_num=100000,
            single_table=False,
            filter_test=None,
        )
        stats = {
            'elapsed': datetime.timedelta(seconds=10),
            'first_received_time': datetime.timedelta(seconds=2),
            'required': 200000,
            'recved': 200000,
            'monitor_wait_time': 1,
            'total_time': 12.34,
            'max_cpu': 400,
            'max_mem': 1024 * 1024 * 1024,
            'min_idle': 50,
            'min_free': 8 * 1024 * 1024 * 1024,
            'cores': 8,
            'memory': 16 * 1024 * 1024 * 1024,
            'tester_errors': 7,
            'tester_timeouts': 11,
        }

        header = [field.strip() for field in bgperf2.stats_header().split(',')]
        row = bgperf2.create_output_stats(args, '0.50.0', stats)

        tester_errors_index = 20
        tester_timeouts_index = 21
        failed_index = 22

        self.assertEqual(len(header), 24)
        self.assertEqual(len(row), 25)
        self.assertEqual(
            header[tester_errors_index:24],
            ['tester errors', 'failed', 'MSG', 'filters'],
        )
        self.assertEqual(row[tester_errors_index], 7)
        self.assertEqual(row[tester_timeouts_index], 11)
        self.assertEqual(row[failed_index:25], ['', '', ''])

    def test_clean_revision_rejects_dirty_checkout(self):
        with mock.patch('subprocess.check_output') as check_output:
            check_output.side_effect = ['abc123\n', ' M rustbgpd.py\n']
            with self.assertRaisesRegex(RuntimeError, 'must be clean'):
                RustBGPd._clean_revision('/repo', 'adapter')

    def test_requested_checkout_must_match_clean_source_head(self):
        with mock.patch('subprocess.check_output', return_value='abc123\n'):
            RustBGPd._require_checkout_at_head('/repo', 'release', 'abc123')

        with mock.patch('subprocess.check_output', return_value='def456\n'):
            with self.assertRaisesRegex(RuntimeError, 'clean source HEAD is abc123'):
                RustBGPd._require_checkout_at_head('/repo', 'release', 'abc123')

    def test_adapter_must_be_tracked(self):
        with mock.patch('subprocess.check_output') as check_output:
            check_output.side_effect = subprocess.CalledProcessError(1, 'git')
            with self.assertRaisesRegex(RuntimeError, 'must be tracked'):
                RustBGPd._require_tracked_file('/repo', '/repo/rustbgpd.py', 'adapter')


if __name__ == '__main__':
    unittest.main()
