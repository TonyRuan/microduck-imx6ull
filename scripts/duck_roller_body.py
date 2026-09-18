"""Run the existing body server with the RL launcher's roller placement/friction.

No protocol or control calculation changes. These two scene settings are otherwise
applied by microduck_rl/scripts/infer_policy.py, not stored in its training XML.
"""
def main():
    import mujoco
    from mjlab_microduck.sim import body_server

    class RollerWorld(body_server.World):
        def __init__(self, scene, count=1):
            if count != 1:
                raise ValueError('The roller panel supports one owned body')
            super().__init__(scene, count)
            for joint in range(self.model.njnt):
                name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint)
                if name and name.startswith('passive_'):
                    self.model.dof_frictionloss[self.model.jnt_dofadr[joint]] = .003

    body_server.HOME_TRUNK_Z = .1385
    body_server.World = RollerWorld
    body_server.main()


if __name__ == '__main__': main()
