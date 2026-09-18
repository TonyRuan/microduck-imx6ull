"""Deploy the streaming actor and compare real ARM responses with ORT fixtures."""
import json
from pathlib import Path
import shlex
import struct
from serial_shell import SerialShell, password

root = Path(__file__).parent
out = root / "out"
shell = SerialShell(password())
try:
    board = "/home/debian/microduck-policy-bench"
    digest = shell.upload(out / "policy-infer-armv7", board + "/policy-infer-armv7")
    shell.command(f"chmod +x {board}/policy-infer-armv7")
    fixtures = (out / "fixtures.bin").read_bytes()
    cases = [0, 1024, 2048, 3072, 4096, 4500, 5000, 5119]
    errors = []
    for index in cases:
        row = struct.unpack_from("<75f", fixtures, index * 300)
        request = " ".join(format(x, ".9g") for x in row[:61])
        reply = shell.command(
            f"cd {board}; printf '%s\\n' {shlex.quote(request)} | ./policy-infer-armv7 weights.bin"
        )
        actual = [float(x) for x in reply.split()]
        assert len(actual) == 14, reply
        for a, b in zip(actual, row[61:]):
            error = abs(a - b)
            assert error <= 1e-4 + 1e-4 * abs(b), (index, a, b)
            errors.append(error)
    result = {"cases": cases, "passed_values": len(errors), "max_abs_error": max(errors),
              "executable_sha256": digest}
    (root / "stream-results.json").write_text(json.dumps(result, indent=2) + "\n")
    for name in ["policy.h", "policy.c", "bench.c", "infer.c"]:
        shell.upload(root / name, board + "/" + name)
    print(json.dumps(result, indent=2))
    print(shell.command(f"ls -lh {board}; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; pgrep -a sha256sum || true"))
finally:
    shell.close()
