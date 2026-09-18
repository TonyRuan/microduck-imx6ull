"""Opt-in board control-thread profiling with an owned, headless MuJoCo body.

Uses the existing HIL transport; never connects physical motor I/O. Run again
with --baseline to measure the same build/workload without stage clock probes.
Raw files and a compact summary are retained in --output after clean shutdown.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import duck_backends as backends
from duck_credentials import BoardCredentials


def distribution(values):
    values = sorted(values)
    if not values:
        return None
    # Nearest-rank percentiles, including outliers (no trimming).
    return dict(mean=sum(values)/len(values), p50=values[math.ceil(.50*len(values))-1],
                p95=values[math.ceil(.95*len(values))-1], p99=values[math.ceil(.99*len(values))-1],
                maximum=values[-1])


def summarize(trace, warmup_s=5., period_ms=20.):
    if trace.get('format') != 1:
        raise ValueError('Unsupported stage trace format')
    rows = [dict(zip(trace['columns'], row)) for row in trace['rows']]
    for row in rows:
        wall, cpu = row['stage_wall_ms'], row['stage_thread_cpu_ms']
        if len(wall) != len(trace['stages']) or not math.isclose(sum(wall), row['work_ms'], abs_tol=1e-8):
            raise ValueError('Stage wall times do not partition the tick')
        if cpu is not None and (len(cpu) != len(wall) or not math.isclose(sum(cpu), row['thread_cpu_ms'], abs_tol=1e-8)):
            raise ValueError('Stage CPU times do not partition the tick')
    selected = [row for row in rows if row['start_elapsed_s'] >= warmup_s and row['driving']]
    if not selected:
        raise ValueError('No steady, driving ticks captured')

    def group(items):
        return dict(count=len(items), work_ms=distribution([r['work_ms'] for r in items]),
                    thread_cpu_ms=distribution([r['thread_cpu_ms'] for r in items if r['thread_cpu_ms'] is not None]),
                    missing_cpu_rows=sum(r['thread_cpu_ms'] is None for r in items),
                    wake_late_ms=distribution([r['wake_late_ms'] for r in items]),
                    scheduled_finish_ms=distribution([r['wake_late_ms']+r['work_ms'] for r in items]),
                    work_over_period=sum(r['work_ms'] > period_ms for r in items),
                    scheduled_finish_over_period=sum(r['wake_late_ms']+r['work_ms'] > period_ms for r in items),
                    stages={name: dict(wall_ms=distribution([r['stage_wall_ms'][i] for r in items]),
                                       thread_cpu_ms=distribution([r['stage_thread_cpu_ms'][i] for r in items
                                                                  if r['stage_thread_cpu_ms'] is not None]))
                            for i, name in enumerate(trace['stages'])})

    return dict(raw_count=len(rows), dropped_rows=trace['dropped_rows'], warmup_s=warmup_s,
                period_ms=period_ms, scope=trace['scope'], steady=group(selected),
                policy_counts={label: sum(r['policy_id'] == i for r in rows)
                               for i, label in enumerate(trace['policies'])},
                by_policy={label: group(items) for i, label in enumerate(trace['policies'])
                           if (items := [r for r in selected if r['policy_id'] == i])})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rl', type=Path, default=ROOT.parent/'microduck_rl')
    parser.add_argument('--port', default=next(iter(backends.board_ports()), ''))
    parser.add_argument('--profile', choices=list(backends.PROFILES), default='alpha')
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--subscribe-hz', type=int, choices=[0, 20], default=20)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    remote = backends.BOARD_DIR + '/profile-' + uuid.uuid4().hex
    captured = {}
    metadata = {}

    class ProfileShell(backends.SerialShell):
        armed = False

        def command(self, command, timeout=30):
            result = super().command(command, timeout)
            if command.startswith('chmod +x '):
                metadata['board_before'] = super().command(
                    'uname -a; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor '
                    '/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq')
                stage_env = ('unset DUCK_HIL_STAGES' if args.baseline else
                             f'export DUCK_HIL_STAGES={remote}-stages.json')
                super().command(f'export DUCK_HIL_TIMINGS={remote}-loop.json; {stage_env}')
                self.armed = True
            return result

        def close(self):
            try:
                if self.armed:
                    # Session.close has waited for robotd and restored the shell.
                    for kind in (['loop'] if args.baseline else ['loop', 'stages']):
                        captured[kind] = json.loads(super().command(f'cat {remote}-{kind}.json', timeout=60))
                    metadata['board_after'] = super().command(
                        'cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq')
            finally:
                super().close()

    password = BoardCredentials().get(args.port)
    if not password:
        raise OSError('No saved board credential; use the panel to save one first')
    client = backends.ManagedClient(args.rl, viewer=False)
    client.select('board', args.port, password=password, policy_profile=args.profile)
    del password
    metadata.update(recorded_at=datetime.now(timezone.utc).isoformat(), profile=args.profile,
                    baseline=args.baseline, subscribe_hz=args.subscribe_hz, remote_prefix=remote,
                    robotd_sha256=hashlib.sha256((backends.EXPERIMENT/'out/robotd-hil').read_bytes()).hexdigest(),
                    scope='Real board control; USB proxy and headless Mac MuJoCo body. No physical motor IO.')
    stages = [('idle', 2., None, 0.), ('walk', 3., None, .4), ('stop', 1., None, 0.),
              ('sit', 3., 'sit_toggle', 0.), ('rise', 3., 'sit_toggle', 0.),
              ('kick_left', 2., 'kick_left', 0.), ('kick_right', 2., 'kick_right', 0.),
              ('ground_pick', 5., 'ground_pick', 0.), ('roulade', 4., 'roulade', 0.),
              ('recovery', 3., None, 0.)]
    metadata['commands'] = []
    with patch.object(backends, 'SerialShell', ProfileShell):
        try:
            client.connect()
            metadata['logs'] = str(client.session.directory)
            metadata['policies'] = client.call('robot.policies')
            metadata['bundle_index_sha256'] = hashlib.sha256((client.session.bundle/'index.json').read_bytes()).hexdigest()
            if args.subscribe_hz:
                client.call('robot.subscribe', {'hz': args.subscribe_hz})
            for name, duration, skill, velocity in stages:
                if skill and skill not in client.skills:
                    continue
                client.move()
                if skill:
                    client.call('robot.do', {'skill': skill})
                start = time.monotonic()
                while time.monotonic()-start < duration:
                    client.move(velocity)
                    time.sleep(.05)
                health = client.call('robot.health')
                metadata['commands'].append(dict(name=name, health=health))
                print(name, round(health['control_loop']['achieved_hz'], 2), 'Hz', flush=True)
        finally:
            client.disconnect()
            for kind, data in captured.items():
                (args.output/(kind+'.json')).write_text(json.dumps(data)+'\n')
            (args.output/'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    required = {'loop'} if args.baseline else {'loop', 'stages'}
    if captured.keys() != required:
        raise RuntimeError('Shutdown did not produce all requested traces; inspect logs')
    loop = captured['loop']
    steady = [dict(zip(loop['columns'], row)) for row in loop['rows']]
    steady = [r['work_ms'] for r in steady if r['start_elapsed_s'] >= 5 and r['driving']]
    summary = dict(metadata=metadata, legacy_work_ms=distribution(steady), legacy_count=len(steady),
                   legacy_work_over_period=sum(value > 20. for value in steady))
    if not args.baseline:
        summary['stages'] = summarize(captured['stages'])
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print('Saved', args.output/'summary.json', flush=True)


if __name__ == '__main__':
    main()
