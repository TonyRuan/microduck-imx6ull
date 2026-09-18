//! Bounded, opt-in HIL measurements. No per-tick logging or file writes.
use std::path::PathBuf;
use std::time::Instant;

pub struct Recorder {
    path: PathBuf,
    rows: Vec<(f64, f64, bool)>,
    first: Instant,
}

impl Recorder {
    pub fn from_env() -> Option<Self> {
        Some(Self {
            path: std::env::var_os("DUCK_HIL_TIMINGS")?.into(),
            rows: Vec::with_capacity(30_000),
            first: Instant::now(),
        })
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
        if let Ok(data) = serde_json::to_vec(&serde_json::json!({
            "columns": ["start_elapsed_s", "work_ms", "driving"],
            "scope": "robotd tick including sensor read, control, safety, actuator write, publication and slow sensors; excludes interval sleep",
            "rows": self.rows,
        })) {
            if let Err(error) = std::fs::write(&self.path, data) {
                tracing::error!(%error, "cannot save HIL timings");
            }
        }
    }
}
