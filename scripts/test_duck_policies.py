"""Full-model deployment must never silently degrade to walk-only."""
import hashlib
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from duck_backends import Session, ManagedClient, BOARD_DIR
from duck_keyboard import Controls
from policy_bundle import MODEL_NAMES, PROFILES, checked_bundle, controller_config, slots, skills_for


class PolicyTests(unittest.TestCase):
    def test_profiles_cover_every_model_and_match_backends(self):
        covered = set()
        for profile in PROFILES:
            host = tomllib.loads(controller_config(profile, '/models'))['policy']
            board = tomllib.loads(controller_config(profile, '/models', native=True))['policy']
            for slot, name in slots(profile).items():
                if name:
                    covered.add(name)
                    self.assertEqual(host[slot], '/models/'+name+'.onnx')
                    self.assertEqual(board[slot], '/models/'+name+'.duckmlp')
                else: self.assertEqual(board[slot], 'none')
            self.assertEqual(host['skill'], board['skill'])
            self.assertEqual(host['ground_pick_period'], 5. if profile == 'roller' else 4.)
        self.assertEqual(covered, set(MODEL_NAMES))
        self.assertEqual(skills_for('roller'), {'sit_toggle', 'ground_pick'})
        self.assertNotIn('roulade', skills_for('roller'))
        self.assertIn('roulade', skills_for('velstand'))

    def test_complete_bundle_checks_both_backends(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            digest = hashlib.sha256(b'test').hexdigest()
            index = {'format': 1, 'models': {name: dict(model_sha256=digest, weights_sha256=digest) for name in MODEL_NAMES}}
            for name in MODEL_NAMES:
                for suffix in ('.onnx', '.duckmlp'):
                    (directory/(name+suffix)).write_bytes(b'test')
            (directory/'index.json').write_text(json.dumps(index))
            checked_bundle(directory)
            (directory/'alpha_stand.duckmlp').write_bytes(b'corrupted')
            with self.assertRaisesRegex(OSError, 'checksum mismatch'): checked_bundle(directory)

    def test_failed_or_fallback_slot_prevents_ready(self):
        session = Session.__new__(Session)
        session.kind, session.policy_profile = 'board', 'alpha'
        report = {'slots': [dict(slot=slot, path=BOARD_DIR+'/'+name+'.duckmlp', error=None)
                            for slot, name in slots('alpha').items()]}
        session.validate_policies(report)
        report['slots'][1]['path'] = '/wrong-directory/alpha_stand.duckmlp'
        with self.assertRaisesRegex(ValueError, 'stand'): session.validate_policies(report)
        report['slots'][1]['path'] = BOARD_DIR+'/alpha_stand.duckmlp'
        report['slots'][1]['error'] = 'failed override'
        with self.assertRaises(ValueError): session.validate_policies(report)

    def test_profile_validation_and_pick_key_repeats(self):
        client = ManagedClient('/example')
        client.select('board', policy_profile='roller')
        self.assertEqual(client.policy_profile, 'roller')
        with self.assertRaises(ValueError): client.select('mac', policy_profile='unknown')
        controls = Controls()
        self.assertEqual(controls.press('p'), 'ground_pick')
        self.assertIsNone(controls.press('p'))


if __name__ == '__main__': unittest.main()
