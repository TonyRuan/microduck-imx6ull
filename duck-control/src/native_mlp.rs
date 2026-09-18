//! Opt-in executor for the fixed graph checked by experiments/imx6ull-policy/export.py.
//! No simulation or controller behavior lives here: this replaces only inference.
use crate::obs::{ACTION_LEN, OBS_LEN};

const FLOATS: usize = 197_896;

pub(crate) struct NativeMlp {
    weights: Box<[f32]>,
}

unsafe extern "C" {
    fn duck_policy_infer(weights: *const f32, obs: *const f32, actions: *mut f32) -> i32;
}

impl NativeMlp {
    pub(crate) fn from_bytes(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() != 8 + FLOATS * 4 || &bytes[..8] != b"DUCKMLP1" {
            return Err("expected a DUCKMLP1 export with 197896 FP32 values".into());
        }
        let weights: Box<[f32]> = bytes[8..]
            .chunks_exact(4)
            .map(|v| f32::from_le_bytes(v.try_into().unwrap()))
            .collect();
        if !weights.iter().all(|v| v.is_finite())
            || weights[OBS_LEN..2 * OBS_LEN].iter().any(|&v| v <= 0.0)
        {
            return Err("non-finite weights or non-positive normalization divisor".into());
        }
        Ok(Self { weights })
    }

    pub(crate) fn infer(&self, obs: &[f32]) -> Result<[f32; ACTION_LEN], String> {
        if obs.len() != OBS_LEN {
            return Err("expected 61 observations".into());
        }
        let mut actions = [0.0; ACTION_LEN];
        // C's compile-time size assertion and this loader agree on the exact layout.
        // Buffers are aligned f32 storage, live for the call, and never retained by C.
        let rc =
            unsafe { duck_policy_infer(self.weights.as_ptr(), obs.as_ptr(), actions.as_mut_ptr()) };
        if rc != 0 {
            return Err("non-finite observation or action".into());
        }
        Ok(actions)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn weights() -> Vec<u8> {
        let mut bytes = vec![0; 8 + FLOATS * 4];
        bytes[..8].copy_from_slice(b"DUCKMLP1");
        for i in OBS_LEN..2 * OBS_LEN {
            bytes[8 + i * 4..12 + i * 4].copy_from_slice(&1.0f32.to_le_bytes());
        }
        bytes
    }

    #[test]
    fn corrupt_exports_do_not_reach_ffi() {
        assert!(NativeMlp::from_bytes(b"short").is_err());
        let mut bytes = weights();
        bytes[8..12].copy_from_slice(&f32::NAN.to_le_bytes());
        assert!(NativeMlp::from_bytes(&bytes).is_err());
        let mut bytes = weights();
        bytes[8 + OBS_LEN * 4..12 + OBS_LEN * 4].fill(0);
        assert!(NativeMlp::from_bytes(&bytes).is_err());
    }

    #[test]
    fn ffi_returns_actions_and_rejects_nonfinite_observations() {
        let net = NativeMlp::from_bytes(&weights()).unwrap();
        assert_eq!(net.infer(&[0.0; OBS_LEN]).unwrap(), [0.0; ACTION_LEN]);
        let mut obs = [0.0; OBS_LEN];
        obs[5] = f32::NAN;
        assert!(net.infer(&obs).is_err());
        assert!(net.infer(&[0.0; 60]).is_err());
    }
}
