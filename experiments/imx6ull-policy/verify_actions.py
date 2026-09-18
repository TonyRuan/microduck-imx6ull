"""Exercise all profiles through the real controller and an owned MuJoCo body.

Records controller transitions, not a claim of physical task success (no ball/object).
Never attaches to or terminates another panel's simulator or serial connection.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from duck_backends import ManagedClient, PROFILES, board_ports


def summarize(report):
    """Keep auditable acceptance metrics without checking megabytes of traces into git."""
    result = {key: value for key, value in report.items() if key != 'profiles'}
    result['profiles'] = {}
    for name, profile in report['profiles'].items():
        entry = {key: value for key, value in profile.items() if key != 'stages'}
        entry['stages'] = []
        for stage in profile['stages']:
            row = {key: value for key, value in stage.items() if key != 'samples'}
            samples = stage['samples']
            tilt = [math.degrees(math.acos(max(-1., min(1., -s['safety']['gravity'][2])))) for s in samples]
            expected = ('stand' if name == 'alpha' else 'walk') if stage['name'] in {'idle', 'stop', 'recovery'} else stage['name']
            row.update(sample_count=len(samples), expected_label=expected,
                       transition_passed=expected in stage['policy_counts'] and 'held' not in stage['policy_counts'],
                       max_tilt_deg=max(tilt), final_tilt_deg=tilt[-1],
                       final_fallen=samples[-1]['safety']['fallen'],
                       target_range_rad=max(max(s['targets'][j] for s in samples)-min(s['targets'][j] for s in samples)
                                            for j in range(len(samples[0]['targets']))))
            entry['stages'].append(row)
        entry['transitions_passed'] = all(row['transition_passed'] for row in entry['stages'])
        entry['upright_at_stage_ends'] = all(not row['final_fallen'] and row['final_tilt_deg'] < 30 for row in entry['stages'])
        result['profiles'][name] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mac', 'board'], required=True)
    parser.add_argument('--rl', type=Path, default=ROOT.parent/'microduck_rl')
    parser.add_argument('--port', default=next(iter(board_ports()), ''))
    parser.add_argument('--profile', choices=list(PROFILES), action='append')
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    report = dict(recorded_at=datetime.now(timezone.utc).isoformat(), backend=args.backend,
                  scope='MuJoCo action transitions, not physical task completion', profiles={})
    for profile in args.profile or PROFILES:
        client = ManagedClient(args.rl, viewer=False)
        client.select(args.backend, args.port, remember=True, policy_profile=profile)
        stream = reader = None
        done = threading.Event()
        try:
            client.connect()
            session = client.session
            record = dict(policies=client.call('robot.policies'), stages=[], logs=str(session.directory))
            binary = ROOT/('target/debug/robotd' if args.backend == 'mac' else 'experiments/imx6ull-policy/out/robotd-hil')
            record['robotd_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            record['bundle_index_sha256'] = hashlib.sha256((session.bundle/'index.json').read_bytes()).hexdigest()
            report['profiles'][profile] = record
            if args.backend == 'board':
                client.call('robot.subscribe', {'hz': 20})
            else:
                stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                stream.connect(str(session.sock))
                stream.settimeout(.3)
                stream.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.subscribe","params":{"hz":20}}\n')
                def read_states():
                    buffer = b''
                    while not done.is_set():
                        try: data = stream.recv(65536)
                        except socket.timeout: continue
                        if not data: return
                        buffer += data
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            message = json.loads(line)
                            if message.get('method') == 'robot.state': session.state = message['params']
                reader = threading.Thread(target=read_states)
                reader.start()
            stages = [('idle', 2., None, 0.), ('walk', 3., None, .4),
                      ('stop', 1., None, 0.), ('sit', 3., 'sit_toggle', 0.),
                      ('rise', 3., 'sit_toggle', 0.), ('kick_left', 2., 'kick_left', 0.),
                      ('kick_right', 2., 'kick_right', 0.), ('ground_pick', 5., 'ground_pick', 0.),
                      ('roulade', 4., 'roulade', 0.), ('recovery', 3., None, 0.)]
            for name, duration, skill, velocity in stages:
                if skill and skill not in client.skills:
                    continue
                samples = []
                client.move()
                accepted = client.call('robot.do', {'skill': skill}) if skill else None
                start = time.monotonic()
                previous_t = None
                while time.monotonic()-start < duration:
                    client.move(velocity)
                    state = session.state
                    if state and state['t_ns'] != previous_t:
                        previous_t = state['t_ns']
                        samples.append({key: state[key] for key in
                                        ('t', 'policy', 'safety', 'loop', 'imu', 'joints', 'targets', 'odom')})
                    time.sleep(.05)
                counts = dict(Counter(s['policy'] for s in samples))
                result = dict(name=name, accepted=accepted, policy_counts=counts,
                              health=client.call('robot.health'), sitting=client.call('robot.policies')['sitting'],
                              samples=samples)
                record['stages'].append(result)
                args.results.write_text(json.dumps(report, indent=2)+'\n')
                print(args.backend, profile, name, counts, 'sitting', result['sitting'],
                      'Hz', round(result['health']['control_loop']['achieved_hz'], 2), flush=True)
                if not samples or any(s['policy'] == 'held' for s in samples):
                    raise RuntimeError('Missing telemetry or controller held after inference failure')
                expected = ('stand' if profile == 'alpha' else 'walk') if name in {'idle', 'stop', 'recovery'} else name
                if expected not in counts:
                    raise RuntimeError('Expected controller transition missing: '+expected)
                if (name == 'sit' and not result['sitting']) or (name == 'rise' and result['sitting']):
                    raise RuntimeError('Sit/stand latch did not change')
        finally:
            done.set()
            if reader: reader.join(timeout=1)
            if stream: stream.close()
            client.disconnect()


if __name__ == '__main__': main()
