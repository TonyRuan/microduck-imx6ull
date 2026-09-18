"""Validate every exported actor on the real ARM CPU, then time short 50 Hz runs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from policy_bundle import MODEL_NAMES, checked_bundle
from serial_shell import SerialShell, password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/cu.usbmodem1234fire56783')
    parser.add_argument('--bundle', type=Path, default=Path(__file__).parent/'out/policies')
    parser.add_argument('--results', type=Path, default=Path(__file__).parent/'all-policy-results.json')
    parser.add_argument('--keychain', action='store_true')
    args = parser.parse_args()
    index = checked_bundle(args.bundle)
    if args.keychain:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
        from duck_credentials import BoardCredentials
        credential = BoardCredentials().get(args.port)
        if not credential: raise SystemExit('No saved board credential')
    else: credential = password()
    shell = SerialShell(credential, port=args.port)
    board = '/home/debian/microduck-policy-bench/all-policies'
    report = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'models': {},
              'scope': 'actor numerical parity and 5-second paced timing; no physical motor I/O'}
    try:
        shell.command(f'mkdir -p {board}')
        shell.upload(Path(__file__).parent/'out/policy-bench-armv7', board+'/bench')
        shell.command(f'chmod +x {board}/bench')
        for name in MODEL_NAMES:
            print('VERIFY', name, flush=True)
            shell.upload(args.bundle/(name+'.duckmlp'), board+'/'+name+'.duckmlp')
            shell.upload(args.bundle/name/'fixtures.bin', board+'/fixtures.bin')
            output = shell.command(f'cd {board}; ./bench {name}.duckmlp fixtures.bin 250 20; printf "BENCH_RC=%s\\n" "$?"', timeout=55)
            rows = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
            if (len(rows) != 2 or rows[0]['failed_values'] or 'BENCH_RC=0' not in output
                    or rows[0]['validation_cases'] != 5120
                    or not rows[0]['rejects_nan'] or not rows[0]['rejects_inf']):
                raise RuntimeError(name+': '+output)
            report['models'][name] = dict(metadata=index['models'][name], validation=rows[0], timing=rows[1])
            args.results.write_text(json.dumps(report, indent=2)+'\n')
            print(name, 'PASS', rows[1]['latency_ms'], flush=True)
    finally: shell.close()


if __name__ == '__main__': main()
