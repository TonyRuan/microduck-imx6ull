//! Bounded, opt-in HIL measurements. No per-tick logging or file writes.
use std::path::PathBuf;
use std::time::Instant;

/// Disjoint intervals, in execution order. Publication stops at queueing; JSON
/// serialization on IPC workers is deliberately outside this control-thread trace.
pub const STAGES: [&str; 11] = [
    "sensor_read",
    "state_estimation",
    "control_prepare",
    "policy_select",
    "observation",
    "inference",
    "control_postprocess",
    "auxiliary",
    "actuator_write",
    "publication",
    "maintenance",
];
pub const POLICIES: [&str; 11] = [
    "held",
    "homing",
    "walk",
    "stand",
    "sit",
    "rise",
    "ground_pick",
    "kick_left",
    "kick_right",
    "roulade",
    "other",
];
const PROFILE_CAPACITY: usize = 6000;

#[derive(Clone, Copy)]
pub enum Stage {
    SensorRead,
    StateEstimation,
    ControlPrepare,
    PolicySelect,
    Observation,
    Inference,
    ControlPostprocess,
    Auxiliary,
    ActuatorWrite,
    Publication,
    Maintenance,
}

fn thread_cpu_ns() -> Option<u64> {
    let mut stamp = std::mem::MaybeUninit::<libc::timespec>::uninit();
    // Read only this OS thread: process CPU would include concurrent IPC workers.
    let result = unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, stamp.as_mut_ptr()) };
    if result != 0 {
        return None;
    }
    let stamp = unsafe { stamp.assume_init() };
    Some(stamp.tv_sec as u64 * 1_000_000_000 + stamp.tv_nsec as u64)
}

pub struct Tick {
    start: Instant,
    last: Instant,
    last_cpu: Option<u64>,
    wall_ns: [u64; 11],
    cpu_ns: [u64; 11],
    cpu_valid: bool,
    wake_late_ms: f64,
    policy: usize,
}

impl Tick {
    fn new(start: Instant, scheduled: Instant) -> Self {
        let cpu = thread_cpu_ns();
        Self {
            start,
            last: start,
            last_cpu: cpu,
            wall_ns: [0; 11],
            cpu_ns: [0; 11],
            cpu_valid: cpu.is_some(),
            wake_late_ms: start.saturating_duration_since(scheduled).as_secs_f64() * 1000.,
            policy: 0,
        }
    }

    pub fn mark(&mut self, stage: Stage) {
        let now = Instant::now();
        let cpu = thread_cpu_ns();
        let index = stage as usize;
        self.wall_ns[index] += now.duration_since(self.last).as_nanos() as u64;
        match (self.last_cpu, cpu) {
            (Some(before), Some(after)) if after >= before => self.cpu_ns[index] += after - before,
            _ => self.cpu_valid = false,
        }
        self.last = now;
        self.last_cpu = cpu;
    }

    pub fn policy(&mut self, label: &str) {
        self.policy = POLICIES
            .iter()
            .position(|name| *name == label)
            .unwrap_or(POLICIES.len() - 1);
    }
}

struct ProfileRow {
    start_elapsed_s: f64,
    driving: bool,
    tick: Tick,
}

struct Profile {
    path: PathBuf,
    rows: Vec<ProfileRow>,
    dropped: u64,
}

pub struct Recorder {
    path: Option<PathBuf>,
    rows: Vec<(f64, f64, bool)>,
    first: Instant,
    profile: Option<Profile>,
}

impl Recorder {
    pub fn from_env() -> Option<Self> {
        let path = std::env::var_os("DUCK_HIL_TIMINGS").map(PathBuf::from);
        let profile_path = std::env::var_os("DUCK_HIL_STAGES").map(PathBuf::from);
        if path.is_none() && profile_path.is_none() {
            return None;
        }
        Some(Self {
            rows: Vec::with_capacity(if path.is_some() { 30_000 } else { 0 }),
            path,
            first: Instant::now(),
            profile: profile_path.map(|path| Profile {
                path,
                rows: Vec::with_capacity(PROFILE_CAPACITY),
                dropped: 0,
            }),
        })
    }

    pub fn begin_tick(&self, start: Instant, scheduled: Instant) -> Option<Tick> {
        self.profile.as_ref().map(|_| Tick::new(start, scheduled))
    }

    pub fn record_profile(&mut self, tick: Option<Tick>, driving: bool) {
        if let (Some(profile), Some(tick)) = (&mut self.profile, tick) {
            if profile.rows.len() < profile.rows.capacity() {
                profile.rows.push(ProfileRow {
                    start_elapsed_s: tick.start.duration_since(self.first).as_secs_f64(),
                    driving,
                    tick,
                });
            } else {
                profile.dropped += 1;
            }
        }
    }

    pub fn record(&mut self, start: Instant, driving: bool) {
        let duration_ms = start.elapsed().as_secs_f64() * 1000.0;
        let elapsed_s = start.duration_since(self.first).as_secs_f64();
        if self.rows.len() < self.rows.capacity() {
            self.rows.push((elapsed_s, duration_ms, driving));
        }
    }
}

impl Drop for Recorder {
    fn drop(&mut self) {
        if let Some(path) = &self.path
            && let Ok(data) = serde_json::to_vec(&serde_json::json!({
                "columns": ["start_elapsed_s", "work_ms", "driving"],
                "scope": "robotd tick including sensor read, control, safety, actuator write, publication and slow sensors; excludes interval sleep",
                "rows": self.rows,
            }))
        {
            if let Err(error) = std::fs::write(path, data) {
                tracing::error!(%error, "cannot save HIL timings");
            }
        }
        if let Some(profile) = &self.profile {
            // Allocate/format only after the loop stops. No labels or JSON in hot-path rows.
            let rows: Vec<_> = profile
                .rows
                .iter()
                .map(|row| {
                    let tick = &row.tick;
                    let wall = tick.wall_ns.map(|ns| ns as f64 / 1e6);
                    let cpu = tick
                        .cpu_valid
                        .then(|| tick.cpu_ns.map(|ns| ns as f64 / 1e6));
                    serde_json::json!([
                        row.start_elapsed_s,
                        row.driving,
                        tick.policy,
                        tick.wake_late_ms,
                        wall.iter().sum::<f64>(),
                        cpu.map(|v| v.iter().sum::<f64>()),
                        wall,
                        cpu,
                    ])
                })
                .collect();
            let value = serde_json::json!({
                "format": 1,
                "scope": "Disjoint control-thread wall and thread-CPU intervals; includes probe overhead; excludes interval sleep, IPC worker serialization and proxy CPU. Wall minus CPU is not exclusively IO wait.",
                "columns": ["start_elapsed_s", "driving", "policy_id", "wake_late_ms", "work_ms", "thread_cpu_ms", "stage_wall_ms", "stage_thread_cpu_ms"],
                "stages": STAGES, "policies": POLICIES,
                "capacity": PROFILE_CAPACITY, "dropped_rows": profile.dropped,
                "rows": rows,
            });
            if let Err(error) = std::fs::write(&profile.path, value.to_string()) {
                tracing::error!(%error, "cannot save HIL stage timings");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn partitions_cover_elapsed_time_and_allow_skipped_stages() {
        let start = Instant::now();
        let mut tick = Tick::new(start, start);
        tick.mark(Stage::SensorRead);
        tick.mark(Stage::Observation);
        tick.mark(Stage::Inference);
        tick.mark(Stage::Maintenance);
        assert_eq!(
            tick.wall_ns.iter().sum::<u64>(),
            tick.last.duration_since(start).as_nanos() as u64
        );
        assert_eq!(tick.wall_ns[Stage::PolicySelect as usize], 0);
        tick.policy("roulade");
        assert_eq!(POLICIES[tick.policy], "roulade");
        tick.policy("custom-skill");
        assert_eq!(POLICIES[tick.policy], "other");
    }

    #[test]
    fn bounded_profile_and_legacy_output_coexist() {
        let dir = tempfile::tempdir().unwrap();
        let legacy = dir.path().join("legacy.json");
        let detailed = dir.path().join("stages.json");
        let now = Instant::now();
        let mut recorder = Recorder {
            path: Some(legacy.clone()),
            rows: Vec::with_capacity(1),
            first: now,
            profile: Some(Profile {
                path: detailed.clone(),
                rows: Vec::with_capacity(1),
                dropped: 0,
            }),
        };
        for _ in 0..2 {
            let start = Instant::now();
            let mut tick = recorder.begin_tick(start, start).unwrap();
            tick.mark(Stage::Maintenance);
            recorder.record(start, true);
            recorder.record_profile(Some(tick), true);
        }
        drop(recorder);
        let read = |path| {
            serde_json::from_slice::<serde_json::Value>(&std::fs::read(path).unwrap()).unwrap()
        };
        assert_eq!(read(legacy)["rows"].as_array().unwrap().len(), 1);
        let report = read(detailed);
        assert_eq!(report["dropped_rows"], 1);
        assert_eq!(report["rows"].as_array().unwrap().len(), 1);
    }
}
