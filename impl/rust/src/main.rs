use std::io::{self, BufRead};
use std::sync::Arc;
use std::sync::mpsc;
use std::time::Duration;

use register_rs::output::{DrainEvent, drain_wait, spawn_writer};
use register_rs::protocol::Server;
use register_rs::shutdown::{SIGNAL_BOUND, Shared, SignalOutcome};
use register_rs::signals;

/// How long the end-of-input path waits for the reply tail; mirrors TypeScript `DRAIN_BOUND_MS`.
const DRAIN_BOUND: Duration = SIGNAL_BOUND;

fn main() {
    // First thing: SIGHUP/SIGINT/SIGTERM reset to default and blocked, so no thread is ever
    // interrupted by them and an inherited ignore cannot swallow them.
    signals::reset_and_block();
    let stdin = io::stdin();
    // One channel for the writer's outcome and a stop signal; created before any thread that
    // holds a sender. The signal thread keeps a sender for the whole run, so it never disconnects.
    let (events_tx, events_rx) = mpsc::channel();
    let shared = Arc::new(Shared::new(Server::new()));
    // A signal runs the same shutdown as end of input, once any request in flight is done.
    let for_signal = Arc::clone(&shared);
    let signal_events = events_tx.clone();
    signals::spawn_waiter(move |sig| {
        match for_signal.finish_on_signal(SIGNAL_BOUND, Server::finish) {
            // The guard stays alive through `exit`, so end of input cannot run a second shutdown.
            SignalOutcome::Finished(_guard) => std::process::exit(128 + sig),
            // Request stuck (e.g. a stalled client): same outcome as SIGKILL.
            SignalOutcome::TimedOut => std::process::exit(128 + sig),
            // End of input got there first (possibly mid-flush, so never exit here): end its
            // bounded wait for the reply tail; main exits 0 once the journal is flushed.
            SignalOutcome::AlreadyShutDown => {
                let _ = signal_events.send(DrainEvent::Stop);
            }
        }
    });
    // A failed write shuts down like end of input would: same lock, so a request in flight
    // finishes first and the journal is never torn.
    let for_failure = Arc::clone(&shared);
    let reply_tx = spawn_writer(
        || io::stdout().lock(),
        events_tx,
        move || {
            let _guard = for_failure.finish_at_eof(Server::finish);
            std::process::exit(0);
        },
    );
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        // The lock covers request handling only, not the reply write: a stalled stdout reader
        // must not burn the signal thread's bound.
        let reply = shared.lock().handle_line(&line);
        if let Some(reply) = reply
            && reply_tx.send(reply).is_err()
        {
            break;
        }
    }
    // Journal first, so nothing but the reply tail can be lost, whatever the reader does. Held
    // through exit: a signal arriving now finds the shutdown done and changes nothing.
    let _guard = shared.finish_at_eof(Server::finish);
    drop(reply_tx);
    // Outcome only decides how long we waited; the exit code is 0 in every case.
    let _ = drain_wait(&events_rx, DRAIN_BOUND);
    std::process::exit(0);
}

#[cfg(test)]
mod seeded_failure {
    #[test]
    fn seeded_failure() {
        panic!("seeded failure (#146)");
    }
}
