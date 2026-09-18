"""The complete local v5 model set and matching controller profiles."""
import hashlib
import json
from pathlib import Path

MODEL_NAMES = (
    'velstand', 'alpha_walking', 'alpha_stand', 'alpha_sitstand',
    'alpha_ground_pick', 'ball_kick_left', 'ball_kick_right', 'roulade',
    'roller', 'roller_crouch',
)
PROFILES = {
    'velstand': ('默认步态 + 全部动作', 'velstand', None, 'alpha_ground_pick', 'walk'),
    'alpha': ('独立站立 + Alpha 行走', 'alpha_walking', 'alpha_stand', 'alpha_ground_pick', 'walk'),
    'roller': ('轮式 + 蹲伏（实验）', 'roller', None, 'roller_crouch', 'roller'),
}
SKILLS = {'sit_toggle', 'ground_pick', 'kick_left', 'kick_right', 'roulade'}


def slots(profile):
    _, walk, stand, pick, _ = PROFILES[profile]
    result = dict(walk=walk, stand=stand, sitstand='alpha_sitstand', ground_pick=pick,
                  kick_left='ball_kick_left', kick_right='ball_kick_right', roulade='roulade')
    # The RL reference launcher rejects kicks/rolls on wheels: these actors were
    # trained for feet. Porting them does not make them roller-compatible.
    if profile == 'roller':
        result.update(kick_left=None, kick_right=None, roulade=None)
    return result


def skills_for(profile):
    return {'sit_toggle', 'ground_pick'} if profile == 'roller' else SKILLS.copy()


def checked_bundle(directory):
    directory = Path(directory)
    try:
        index = json.loads((directory/'index.json').read_text())
        if index['format'] != 1 or set(index['models']) != set(MODEL_NAMES):
            raise ValueError('incomplete model set')
        for name in MODEL_NAMES:
            for suffix, digest in [('.onnx', 'model_sha256'), ('.duckmlp', 'weights_sha256')]:
                data = (directory/(name+suffix)).read_bytes()
                if hashlib.sha256(data).hexdigest() != index['models'][name][digest]:
                    raise ValueError('checksum mismatch: '+name+suffix)
        return index
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise OSError('完整策略包缺失或校验失败，请运行 prepare_policies.py：'+str(error)) from error


def controller_config(profile, directory, native=False):
    """Explicit slots prevent a missing model from falling back to board defaults."""
    mode = PROFILES[profile][4]
    extension = '.duckmlp' if native else '.onnx'
    config = '[policy]\nenabled = true\nmode = '+json.dumps(mode)+'\n'
    for slot, name in slots(profile).items():
        config += slot+' = '+json.dumps(str(Path(directory)/(name+extension)) if name else 'none')+'\n'
    # Match the pinned manifest even when no production set is installed at /opt.
    config += f'ground_pick_period = {5.0 if mode == "roller" else 4.0}\n'
    for name, duration, chain in [('roulade', 1.0, True), ('kick_left', .5, False), ('kick_right', .5, False)]:
        config += '\n[[policy.skill]]\nname = '+json.dumps(name)+f'\nduration = {duration}\nchain = {str(chain).lower()}\n'
    config += '\n[audio]\nenabled = false\npet_detect = false\n[chorale]\naccept = false\n'
    return config
