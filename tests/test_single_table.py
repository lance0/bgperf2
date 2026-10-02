'''`-s/--single-table` is refused at every entry point (bgperf2-0ma).

No target implemented it: BIRD's only reader was a per-neighbour config path
nothing had run since 2021, and every other daemon never read it. The baseline
published `bird -s` and `bird` as two configurations that were one, so the flag
is refused rather than accepted -- on all four paths, since a refusal applied
to three is a silent acceptance on the fourth.
'''
from argparse import Namespace

import pytest

import bgperf2
from bird import BIRDTarget


def a_test(**overrides):
    test = {'name': 'scale', 'neighbors': [10], 'prefixes': [100],
            'filter_test': ['None'], 'targets': [{'name': 'bird'}]}
    test.update(overrides)
    return test


def test_bench_refuses_the_flag_before_anything_else():
    # Only what the guard reads: a refusal that needed more would have run
    # some other code first.
    args = Namespace(target='bird', single_table=True, file=None, dir='/tmp', bench_name='x',
                     docker_network_name=None)
    with pytest.raises(SystemExit) as raised:
        bgperf2.bench(args)
    assert '-s/--single-table is refused' in str(raised.value)


def test_bench_refuses_the_flag_beside_a_scenario_file_too():
    args = Namespace(target='bird', single_table=True, file='scenario.yaml', dir='/tmp',
                     bench_name='x', docker_network_name=None)
    with pytest.raises(SystemExit) as raised:
        bgperf2.bench(args)
    assert '-s/--single-table is refused' in str(raised.value)


def test_a_scenario_asking_for_it_is_refused_before_the_teardown(tmp_path, monkeypatch):
    scenario = tmp_path / 'scenario.yaml'
    scenario.write_text('target: {as: 1000, single-table: true}\n')
    torn_down = []
    monkeypatch.setattr(bgperf2, 'remove_target_containers',
                        lambda: torn_down.append(True))
    args = Namespace(target='bird', single_table=False, file=str(scenario), dir=str(tmp_path),
                     bench_name='x', docker_network_name=None, repeat=True)
    with pytest.raises(SystemExit) as raised:
        bgperf2.bench(args)
    assert "scenario's target `single-table` is refused" in str(raised.value)
    assert not torn_down


def test_config_refuses_the_flag():
    args = Namespace(target='bird', single_table=True, neighbor_num=1, prefix_num=1)
    with pytest.raises(SystemExit) as raised:
        bgperf2.config(args)
    assert '-s/--single-table is refused' in str(raised.value)


def test_a_batch_target_asking_for_it_is_refused():
    test = a_test(targets=[{'name': 'bird', 'label': 'bird -s',
                            'single_table': True}])
    with pytest.raises(SystemExit) as raised:
        bgperf2.check_batch_test(test)
    assert "target 'bird -s' carries single_table" in str(raised.value)


@pytest.mark.parametrize('value', [False, None])
def test_the_default_spelled_out_still_runs(value):
    '''An older batch file that writes the default out asks for nothing.'''
    bgperf2.check_batch_test(a_test(targets=[{'name': 'bird',
                                              'single_table': value}]))
    bgperf2.refuse_single_table(value, 'x')


def test_the_scenario_no_longer_carries_the_key():
    args = Namespace(
        neighbor_num=1, prefix_num=1, filter_type='in', as_path_list_num=0,
        prefix_list_num=0, community_list_num=0, ext_community_list_num=0,
        single_table=False, target_config_file=None,
        local_address_prefix='10.10.0.0/16', target_local_address=None,
        target_router_id=None, monitor_local_address=None,
        monitor_router_id=None, filter_test=None, license_file=None,
        mrt_file=None, tester_type='bird')
    assert 'single-table' not in bgperf2.gen_conf(args)


def test_bird_renders_without_the_key(tmp_path):
    '''What `gen_conf()` now writes, and what an older scenario still says.'''
    for conf in ({}, {'single-table': False}):
        target = object.__new__(BIRDTarget)
        target.conf = dict(conf, **{'as': 1000, 'router-id': '10.10.255.254',
                                    'local-address': '10.10.255.254'})
        target.host_dir = str(tmp_path)
        target.scenario_global_conf = {'testers': [], 'monitor': {
            'as': 1001, 'router-id': '10.10.0.2', 'local-address': '10.10.0.2'}}
        target.write_config()
        config = (tmp_path / BIRDTarget.CONFIG_FILE_NAME).read_text()
        assert 'neighbor range 10.0.0.0/8 external;' in config
        assert 'protocol pipe' not in config


def test_the_row_flags_column_stays_and_is_empty(bench_args, bench_stats):
    header = bgperf2.stats_header().split(',')
    row = bgperf2.create_output_stats(bench_args, 'v', bench_stats)
    assert row[[c.strip() for c in header].index('flags')] == ''


def test_a_batch_file_target_asking_for_it_is_refused_before_any_cell(tmp_path):
    '''Refused at its cell, the SystemExit would leave batch() hours in.'''
    scenario = tmp_path / 'old.yaml'
    scenario.write_text('target: {as: 1000, single-table: true}\n')
    test = a_test(targets=[{'name': 'bird', 'file': str(scenario)}])
    with pytest.raises(SystemExit) as raised:
        bgperf2.check_batch_test(test)
    assert "scenario file" in str(raised.value)
    assert 'sets single-table, which is refused' in str(raised.value)


def test_a_scenario_asking_for_nothing_passes(tmp_path):
    scenario = tmp_path / 'ok.yaml'
    scenario.write_text('target: {as: 1000, single-table: false}\n')
    bgperf2.check_batch_test(a_test(targets=[{'name': 'bird',
                                              'file': str(scenario)}]))


def test_a_scenarios_bad_receivers_are_refused_before_the_teardown(tmp_path, monkeypatch):
    '''bgperf2-urp: this used to run after the previous run was torn down.'''
    scenario = tmp_path / 'scenario.yaml'
    scenario.write_text('target: {as: 1000}\nreceivers: 3\n')
    torn_down = []
    monkeypatch.setattr(bgperf2, 'remove_target_containers',
                        lambda: torn_down.append(True))
    args = Namespace(target='bird', single_table=False, file=str(scenario), dir=str(tmp_path),
                     bench_name='x', docker_network_name=None, repeat=True)
    with pytest.raises(SystemExit) as raised:
        bgperf2.bench(args)
    assert 'must be a list of sessions' in str(raised.value)
    assert not torn_down


@pytest.fixture
def no_runtime_side_effects(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('invalid request reached Docker or teardown')

    monkeypatch.setattr(bgperf2, 'install_stop_handlers', lambda: None)
    monkeypatch.setattr(bgperf2, 'start_interruption_watch', lambda: None)
    for name in ('target_image', 'check_batch_images',
                 'remove_target_containers', 'remove_old_containers'):
        monkeypatch.setattr(bgperf2, name, unexpected)
    monkeypatch.setattr(bgperf2.GoBGP, 'require_image', unexpected)


@pytest.mark.parametrize('command', ['bench', 'config'])
def test_cli_refusal_precedes_docker_and_output(
        command, tmp_path, no_runtime_side_effects):
    output = tmp_path / 'output'
    args = bgperf2.create_args_parser().parse_args(
        [command, '-s', '-o', str(output)])
    with pytest.raises(SystemExit, match='no target implements it'):
        args.func(args)
    assert not output.exists()


@pytest.mark.parametrize('value', ['true', '"false"'])
def test_scenario_refusal_precedes_monitor_image_preflight(
        value, tmp_path, no_runtime_side_effects):
    scenario = tmp_path / 'scenario.yaml'
    scenario.write_text('target: {single-table: ' + value + '}\n')
    args = bgperf2.create_args_parser().parse_args(['bench', '-f', str(scenario)])
    with pytest.raises(SystemExit, match='no target implements it'):
        args.func(args)


@pytest.mark.parametrize('value', [True, 'false'])
@pytest.mark.parametrize('from_file', [False, True])
def test_entire_batch_is_refused_before_image_checks_or_first_cell(
        value, from_file, tmp_path, no_runtime_side_effects):
    import yaml

    target = {'name': 'bird', 'single_table': value}
    if from_file:
        scenario = tmp_path / 'scenario.yaml'
        scenario.write_text(yaml.safe_dump({'target': {'single-table': value}}))
        target = {'name': 'bird', 'file': str(scenario)}
    matrix = tmp_path / 'batch.yaml'
    matrix.write_text(yaml.safe_dump({'tests': [
        a_test(name='valid first'),
        a_test(name='invalid later', targets=[target]),
    ]}))
    args = bgperf2.create_args_parser().parse_args(
        ['batch', '-c', str(matrix), '--results-dir', str(tmp_path)])
    with pytest.raises(SystemExit, match='no target implements it'):
        args.func(args)


@pytest.mark.parametrize('explicit_false', [False, True])
def test_default_and_explicit_false_emit_ordinary_configuration(
        explicit_false, tmp_path, no_runtime_side_effects):
    output = tmp_path / 'scenario.yaml'
    args = bgperf2.create_args_parser().parse_args(
        ['config', '-n', '2', '-p', '3', '-o', str(output)])
    if explicit_false:
        args.single_table = False
    else:
        del args.single_table
    args.func(args)
    scenario = bgperf2.render_scenario(output.read_text())
    assert 'single-table' not in scenario['target']
    assert sum(len(tester['neighbors']) for tester in scenario['testers']) == 2


def test_active_example_matrices_do_not_request_the_retired_mode():
    import yaml
    from conftest import REPO_ROOT

    for name in ('bench-bird.yaml', 'big-tests.yaml'):
        matrix = yaml.safe_load((REPO_ROOT / 'benchmarks' / name).read_text())
        for test in matrix['tests']:
            bgperf2.check_batch_test(test)
            assert all(not target.get('single_table') for target in test['targets'])


def test_bench_consumes_the_scenario_it_validated(tmp_path, monkeypatch):
    marker = tmp_path / 'renders'
    scenario = tmp_path / 'scenario.yaml'
    scenario.write_text('''<%
from pathlib import Path
marker = Path({marker!r})
count = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(count))
%>
target: {{single-table: ${{"true" if count > 1 else "false"}}}}
'''.format(marker=str(marker)))
    consumed = []

    class ScenarioConsumed(Exception):
        pass

    def capture(args, conf):
        consumed.append(conf)
        raise ScenarioConsumed

    for name in ('install_stop_handlers', 'start_interruption_watch',
                 'remove_target_containers', 'warn_if_machine_is_busy',
                 'warn_if_log_dir_is_in_ram', 'warn_if_log_dir_is_short_on_space'):
        monkeypatch.setattr(bgperf2, name, lambda *args: None)
    monkeypatch.setattr(bgperf2.GoBGP, 'require_image', lambda *args: 'monitor-image')
    monkeypatch.setattr(bgperf2, 'warn_if_trace_io_reaches_no_generator', capture)
    args = bgperf2.create_args_parser().parse_args(
        ['bench', '-f', str(scenario), '-r'])
    with pytest.raises(ScenarioConsumed):
        args.func(args)
    assert marker.read_text() == '1'
    assert consumed == [{'target': {'single-table': False}}]


@pytest.mark.parametrize('separate_tests', [False, True])
def test_batch_consumes_validated_scenario_copies(
        separate_tests, tmp_path, monkeypatch):
    import copy
    import yaml

    marker = tmp_path / 'renders'
    scenario = tmp_path / 'scenario.yaml'
    scenario.write_text('''<%
from pathlib import Path
marker = Path({marker!r})
count = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(count))
%>
target: {{single-table: ${{"true" if count > 3 else "false"}}}}
'''.format(marker=str(marker)))
    matrix = tmp_path / 'batch.yaml'
    targets = [{'name': 'bird', 'file': str(scenario)}]
    tests = ([a_test(name='first', neighbors=[1], targets=targets),
              a_test(name='second', neighbors=[2], targets=targets)] if separate_tests
             else [a_test(neighbors=[1, 2], targets=targets)])
    matrix.write_text(yaml.safe_dump({'tests': tests}))
    consumed, effects = [], []

    class ScenarioConsumed(Exception):
        pass

    def capture(args, conf):
        consumed.append((args.neighbor_num, copy.deepcopy(conf)))
        # A runtime mutation in one cell must not alter the next cell's input.
        conf['target']['from_previous_cell'] = True
        raise ScenarioConsumed

    original_bench = bgperf2.bench

    def run_cell(args):
        try:
            original_bench(args)
        except ScenarioConsumed:
            return [bgperf2.run_name(args)]

    for name in ('install_stop_handlers', 'start_interruption_watch',
                 'warn_if_machine_is_busy', 'warn_if_log_dir_is_in_ram',
                 'warn_if_log_dir_is_short_on_space'):
        monkeypatch.setattr(bgperf2, name, lambda *args: None)
    monkeypatch.setattr(bgperf2, 'remove_target_containers',
                        lambda: effects.append('teardown'))
    monkeypatch.setattr(bgperf2, 'remove_old_containers', lambda: None)
    monkeypatch.setattr(bgperf2, 'check_batch_images',
                        lambda *args: effects.append('images'))
    monkeypatch.setattr(bgperf2.GoBGP, 'require_image', lambda *args: 'monitor-image')
    monkeypatch.setattr(bgperf2, 'warn_if_trace_io_reaches_no_generator', capture)
    monkeypatch.setattr(bgperf2, 'bench', run_cell)
    monkeypatch.setattr(bgperf2, 'create_batch_graphs', lambda *args, **kwargs: None)
    args = bgperf2.create_args_parser().parse_args(
        ['-d', str(tmp_path), 'batch', '-c', str(matrix),
         '--results-dir', str(tmp_path)])
    for invocation in (1, 2):
        consumed.clear()
        effects.clear()
        args.func(args)
        assert marker.read_text() == str(invocation)
        assert consumed == [(1, {'target': {'single-table': False}}),
                            (2, {'target': {'single-table': False}})]
        assert effects == ['images', 'teardown', 'teardown']
    for progress in tmp_path.glob('*.progress.json'):
        assert '_batch_scenario' not in progress.read_text()
