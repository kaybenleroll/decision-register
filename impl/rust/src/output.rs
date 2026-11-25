//! Reply output on its own thread, so a stalled stdout reader cannot block shutdown.
//!
//! Main sends each reply over a channel; the writer thread writes and flushes them in order. After
//! end of input main flushes the journal first (`finish_at_eof`), closes the reply channel, then
//! waits at most a bound for the writer's `Done` (`drain_wait`). Only the reply tail can be lost.

use std::io::Write;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::time::Duration;

/// What the main thread waits for after end of input.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DrainEvent {
    /// Every reply was written and flushed.
    Done,
    /// A write or flush failed (the reader went away).
    Failed,
    /// A stop signal arrived during the wait: end it now.
    Stop,
}

/// How the wait ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DrainOutcome {
    Drained,
    Failed,
    TimedOut,
    Stopped,
}

/// Starts the writer thread. It creates its writer itself (`StdoutLock` is `!Send`), writes each
/// reply plus a newline and flushes, in order. When the reply channel closes it sends `Done`; on a
/// write error it sends `Failed`, runs `on_failure` and stops.
pub fn spawn_writer<W: Write>(
    make_out: impl FnOnce() -> W + Send + 'static,
    events: Sender<DrainEvent>,
    on_failure: impl FnOnce() + Send + 'static,
) -> Sender<String> {
    let (tx, rx) = mpsc::channel::<String>();
    std::thread::spawn(move || {
        let mut out = make_out();
        for reply in rx {
            if writeln!(out, "{reply}").is_err() || out.flush().is_err() {
                let _ = events.send(DrainEvent::Failed);
                on_failure();
                return;
            }
        }
        let _ = events.send(DrainEvent::Done);
    });
    tx
}

/// Waits up to `bound` for the first event.
pub fn drain_wait(events: &Receiver<DrainEvent>, bound: Duration) -> DrainOutcome {
    match events.recv_timeout(bound) {
        Ok(DrainEvent::Done) => DrainOutcome::Drained,
        Ok(DrainEvent::Failed) => DrainOutcome::Failed,
        Ok(DrainEvent::Stop) => DrainOutcome::Stopped,
        Err(RecvTimeoutError::Timeout) => DrainOutcome::TimedOut,
        // Cannot happen while a sender is held; treated as a finished writer.
        Err(RecvTimeoutError::Disconnected) => DrainOutcome::Drained,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};
    use std::time::Instant;

    const SHORT: Duration = Duration::from_millis(50);

    /// Collects bytes; shared so the test can read them.
    #[derive(Clone, Default)]
    struct Sink(Arc<Mutex<Vec<u8>>>);

    impl Write for Sink {
        fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(b);
            Ok(b.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    /// Blocks in `write` until released (a reader that never reads).
    struct Stalled(Receiver<()>);

    impl Write for Stalled {
        fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
            let _ = self.0.recv();
            Ok(b.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    struct Broken;

    impl Write for Broken {
        fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
            Err(std::io::ErrorKind::BrokenPipe.into())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn stalled_writer_times_out_after_the_bound() {
        let (release, gate) = mpsc::channel::<()>();
        let (events_tx, events_rx) = mpsc::channel();
        let replies = spawn_writer(move || Stalled(gate), events_tx, || {});
        replies.send("one".into()).unwrap();
        drop(replies);
        let start = Instant::now();
        assert_eq!(drain_wait(&events_rx, SHORT), DrainOutcome::TimedOut);
        assert!(start.elapsed() >= SHORT);
        assert!(start.elapsed() < Duration::from_secs(5));
        drop(release);
    }

    #[test]
    fn fast_writer_drains_in_order() {
        let sink = Sink::default();
        let (events_tx, events_rx) = mpsc::channel();
        let s = sink.clone();
        let replies = spawn_writer(move || s, events_tx, || {});
        for r in ["a", "b", "c"] {
            replies.send(r.into()).unwrap();
        }
        drop(replies);
        assert_eq!(
            drain_wait(&events_rx, Duration::from_secs(5)),
            DrainOutcome::Drained
        );
        assert_eq!(sink.0.lock().unwrap().as_slice(), b"a\nb\nc\n");
    }

    #[test]
    fn write_error_reports_failed_and_runs_on_failure() {
        let (events_tx, events_rx) = mpsc::channel();
        let (ran_tx, ran_rx) = mpsc::channel();
        let replies = spawn_writer(
            || Broken,
            events_tx,
            move || {
                ran_tx.send(()).unwrap();
            },
        );
        replies.send("x".into()).unwrap();
        assert_eq!(
            drain_wait(&events_rx, Duration::from_secs(5)),
            DrainOutcome::Failed
        );
        ran_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("on_failure must run");
    }

    #[test]
    fn stop_returns_at_once_even_with_a_stalled_writer() {
        let (release, gate) = mpsc::channel::<()>();
        let (events_tx, events_rx) = mpsc::channel();
        let replies = spawn_writer(move || Stalled(gate), events_tx.clone(), || {});
        replies.send("one".into()).unwrap();
        drop(replies);
        events_tx.send(DrainEvent::Stop).unwrap();
        let start = Instant::now();
        assert_eq!(
            drain_wait(&events_rx, Duration::from_secs(30)),
            DrainOutcome::Stopped
        );
        assert!(start.elapsed() < Duration::from_secs(5));
        drop(release);
    }
}
