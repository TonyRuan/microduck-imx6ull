"""Build guards must reject scalar fallback and unsafe floating-point modes."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('cc'), 'requires a C compiler')
class PolicyBuildTests(unittest.TestCase):
    def test_precise_host_build_is_accepted(self):
        result = subprocess.run(['cc', '-fsyntax-only', '-ffp-contract=off',
                                 str(Path(__file__).with_name('policy.c'))],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def check_rejected(self, flags, diagnostic):
        source = Path(__file__).with_name('policy.c')
        result = subprocess.run(['cc', '-fsyntax-only', *flags, str(source)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(diagnostic, result.stderr)

    def test_required_neon_cannot_fall_back_to_scalar(self):
        self.check_rejected(['-DDUCK_REQUIRE_NEON=1', '-U__ARM_NEON', '-U__ARM_NEON__'],
                            'compiler disabled NEON')

    def test_fast_math_is_rejected(self):
        self.check_rejected(['-ffast-math'], 'fast/finite-only math is forbidden')

    def test_finite_only_math_is_rejected(self):
        self.check_rejected(['-ffinite-math-only'], 'fast/finite-only math is forbidden')


if __name__ == '__main__':
    unittest.main()
