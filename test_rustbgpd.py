import argparse
import datetime
import subprocess
import unittest
from unittest import mock

import bgperf2
from rustbgpd import (
    DOCKERFILE_CONTENT,
    DOCKERFILE_CONTENT_DHAT,
    DOCKERFILE_CONTENT_PROF,
    RustBGPd,
    RustBGPdTarget,
)


class RustBgpdAdapterTests(unittest.TestCase):
    def test_all_build_profiles_use_root_lockfile(self):
        for dockerfile in (
            DOCKERFILE_CONTENT,
            DOCKERFILE_CONTENT_PROF,
            DOCKERFILE_CONTENT_DHAT,
        ):
            self.assertIn('cargo build --workspace', dockerfile)
            self.assertIn('--locked', dockerfile)

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

    def test_csv_quirk_remains_explicit_and_deterministic(self):
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
            'tester_errors': 0,
            'tester_timeouts': 0,
        }

        header = [field.strip() for field in bgperf2.stats_header().split(',')]
        row = bgperf2.create_output_stats(args, '0.50.0', stats)

        self.assertEqual(len(header), 24)
        self.assertEqual(len(row), 25)
        self.assertEqual(header[20:22], ['tester errors', 'failed'])
        self.assertEqual(row[20:22], [0, 0])

    def test_clean_revision_rejects_dirty_checkout(self):
        with mock.patch('subprocess.check_output') as check_output:
            check_output.side_effect = ['abc123\n', ' M rustbgpd.py\n']
            with self.assertRaisesRegex(RuntimeError, 'must be clean'):
                RustBGPd._clean_revision('/repo', 'adapter')

    def test_adapter_must_be_tracked(self):
        with mock.patch('subprocess.check_output') as check_output:
            check_output.side_effect = subprocess.CalledProcessError(1, 'git')
            with self.assertRaisesRegex(RuntimeError, 'must be tracked'):
                RustBGPd._require_tracked_file('/repo', '/repo/rustbgpd.py', 'adapter')


if __name__ == '__main__':
    unittest.main()
