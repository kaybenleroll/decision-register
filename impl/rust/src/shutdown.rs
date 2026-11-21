//! Once-only shutdown shared by the end-of-input path and the signal thread.
//!
//! One mutex guards the state (the server). `finished` records that the shutdown ran. Whoever
//! runs it first keeps the guard until the process exits, so the other path can never run it a
//! second time or override the exit code: end of input first gives exit 0, a signal first gives
//! 128 + the signal number.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, MutexGuard, TryLockError};
use std::time::{Duration, Instant};

/// How long the signal thread waits for a request in flight. On expiry the outcome equals
/// SIGKILL's (no flush, no log, pid file left), so a longer bound can only help.
pub const SIGNAL_BOUND: Duration = Duration::from_secs(5);

const POLL: Duration = Duration::from_millis(1);

pub struct Shared<T> {
    state: Mutex<T>,
    finished: AtomicBool,
}

/// What the signal path found.
pub enum SignalOutcome<'a, T> {
    /// The shutdown ran here; hold the guard until `exit`.
    Finished(MutexGuard<'a, T>),
    /// The end-of-input path got there first (or is running it): do nothing, keep its exit code.
    AlreadyShutDown,
    /// A request stayed in flight for the whole bound: exit without a flush.
    TimedOut,
}

impl<T> Shared<T> {
    pub fn new(state: T) -> Self {
        Self {
            state: Mutex::new(state),
            finished: AtomicBool::new(false),
        }
    }

    /// Locks for one request. A panic in an earlier request does not wedge shutdown.
    pub fn lock(&self) -> MutexGuard<'_, T> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn is_finished(&self) -> bool {
        self.finished.load(Ordering::SeqCst)
    }

    /// End-of-input path: runs `finish` unless it already ran, and returns the guard, which the
    /// caller holds through `exit`.
    pub fn finish_at_eof(&self, finish: impl FnOnce(&mut T)) -> MutexGuard<'_, T> {
        let mut guard = self.lock();
        if !self.finished.swap(true, Ordering::SeqCst) {
            finish(&mut guard);
        }
        guard
    }

    /// Signal path: waits up to `bound` for a request in flight, then runs `finish` once.
    pub fn finish_on_signal(
        &self,
        bound: Duration,
        finish: impl FnOnce(&mut T),
    ) -> SignalOutcome<'_, T> {
        let start = Instant::now();
        loop {
            match self.state.try_lock() {
                Ok(guard) => return self.run_once(guard, finish),
                Err(TryLockError::Poisoned(e)) => return self.run_once(e.into_inner(), finish),
                Err(TryLockError::WouldBlock) => {}
            }
            if self.is_finished() {
                return SignalOutcome::AlreadyShutDown;
            }
            if start.elapsed() >= bound {
                return SignalOutcome::TimedOut;
            }
            std::thread::sleep(POLL);
        }
    }

    fn run_once<'a>(
        &'a self,
        mut guard: MutexGuard<'a, T>,
        finish: impl FnOnce(&mut T),
    ) -> SignalOutcome<'a, T> {
        if self.finished.swap(true, Ordering::SeqCst) {
            return SignalOutcome::AlreadyShutDown;
        }
        finish(&mut guard);
        SignalOutcome::Finished(guard)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::mpsc;

    const SHORT: Duration = Duration::from_millis(50);

    #[derive(Default)]
    struct Probe {
        finishes: u32,
    }

    fn finish(p: &mut Probe) {
        p.finishes += 1;
    }

    #[test]
    fn shutdown_twice_from_eof_runs_once() {
        let shared = Shared::new(Probe::default());
        drop(shared.finish_at_eof(finish));
        drop(shared.finish_at_eof(finish));
        assert_eq!(shared.lock().finishes, 1);
    }

    #[test]
    fn shutdown_twice_from_signal_runs_once() {
        let shared = Shared::new(Probe::default());
        match shared.finish_on_signal(SHORT, finish) {
            SignalOutcome::Finished(g) => drop(g),
            _ => panic!("first signal should run the shutdown"),
        }
        assert!(matches!(
            shared.finish_on_signal(SHORT, finish),
            SignalOutcome::AlreadyShutDown
        ));
        assert_eq!(shared.lock().finishes, 1);
    }

    #[test]
    fn eof_then_signal_leaves_the_exit_to_eof() {
        let shared = Shared::new(Probe::default());
        // EOF path holds the guard through exit: the signal thread cannot take it.
        let guard = shared.finish_at_eof(finish);
        let start = Instant::now();
        let outcome = shared.finish_on_signal(Duration::from_secs(30), finish);
        assert!(matches!(outcome, SignalOutcome::AlreadyShutDown));
        assert!(
            start.elapsed() < Duration::from_secs(5),
            "must not wait out the bound"
        );
        assert_eq!(guard.finishes, 1);
    }

    #[test]
    fn signal_then_eof_runs_once_and_eof_waits_for_exit() {
        let shared = Arc::new(Shared::new(Probe::default()));
        let SignalOutcome::Finished(guard) = shared.finish_on_signal(SHORT, finish) else {
            panic!("signal first should run the shutdown");
        };
        // The EOF path now blocks on the lock until the "process exits" (guard dropped).
        let (tx, rx) = mpsc::channel();
        let eof = {
            let shared = Arc::clone(&shared);
            std::thread::spawn(move || {
                let g = shared.finish_at_eof(finish);
                tx.send(g.finishes).unwrap();
            })
        };
        assert!(
            rx.recv_timeout(SHORT).is_err(),
            "EOF path must wait while the signal holds the guard"
        );
        drop(guard);
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap(), 1);
        eof.join().unwrap();
    }

    #[test]
    fn lock_held_past_the_bound_times_out_without_finishing() {
        let shared = Shared::new(Probe::default());
        let held = shared.lock(); // a request "in flight"
        let start = Instant::now();
        let outcome = shared.finish_on_signal(SHORT, finish);
        assert!(matches!(outcome, SignalOutcome::TimedOut));
        assert!(start.elapsed() >= SHORT, "must wait out the whole bound");
        assert!(start.elapsed() < Duration::from_secs(5));
        assert_eq!(held.finishes, 0, "no flush on expiry");
        assert!(!shared.is_finished());
    }

    #[test]
    fn lock_released_inside_the_bound_finishes() {
        let shared = Arc::new(Shared::new(Probe::default()));
        let (ready_tx, ready_rx) = mpsc::channel();
        let releaser = {
            let shared = Arc::clone(&shared);
            std::thread::spawn(move || {
                let _held = shared.lock(); // a request "in flight" for 30 ms
                ready_tx.send(()).unwrap();
                std::thread::sleep(Duration::from_millis(30));
            })
        };
        ready_rx.recv().unwrap();
        let SignalOutcome::Finished(guard) =
            shared.finish_on_signal(Duration::from_secs(5), finish)
        else {
            panic!("request finished inside the bound: shutdown must run");
        };
        assert_eq!(guard.finishes, 1);
        releaser.join().unwrap();
    }

    #[test]
    fn poisoned_lock_still_shuts_down() {
        let shared = Arc::new(Shared::new(Probe::default()));
        let s2 = Arc::clone(&shared);
        let _ = std::thread::spawn(move || {
            let _g = s2.lock();
            panic!("request panicked");
        })
        .join();
        assert!(matches!(
            shared.finish_on_signal(SHORT, finish),
            SignalOutcome::Finished(_)
        ));
    }
}
