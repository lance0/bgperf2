from tester import BIRDTester


def test_bird_tester_ignores_normal_remote_events(tmp_path):
    (tmp_path / 'peer.log').write_text(
        '\n'.join(
            [
                '2026-08-30 <RMT> bgp1: Received: Connection collision resolution',
                '2026-08-30 <RMT> bgp1: Invalid route withdrawn',
                '2026-08-30 <RMT> bgp1: NEXT_HOP attribute invalid',
                '2026-08-30 <INFO> bgp1: harmless informational event',
                '2026-08-30 <RMT> bgp1: Hold timer expired',
            ]
        )
        + '\n'
    )

    assert BIRDTester.find_errors([tmp_path]) == 1
