use std::io::{self, BufRead, Write};
use std::sync::Arc;

use register_rs::protocol::Server;
use register_rs::shutdown::{SIGNAL_BOUND, Shared, SignalOutcome};
use register_rs::signals;

fn main() {
    // First thing: SIGHUP/SIGINT/SIGTERM reset to default and blocked, so no thread is ever
    // interrupted by them and an inherited ignore cannot swallow them.
    signals::reset_and_block();
    let stdin = io::stdin();
    let mut out = io::stdout().lock();
    let shared = Arc::new(Shared::new(Server::new()));
    // A signal runs the same shutdown as end of input, once any request in flight is done.
    let for_signal = Arc::clone(&shared);
    signals::spawn_waiter(move |sig| {
        match for_signal.finish_on_signal(SIGNAL_BOUND, Server::finish) {
            // The guard stays alive through `exit`, so end of input cannot run a second shutdown.
            SignalOutcome::Finished(_guard) => std::process::exit(128 + sig),
            // Request stuck (e.g. a stalled client): same outcome as SIGKILL.
            SignalOutcome::TimedOut => std::process::exit(128 + sig),
            // End of input got there first and is exiting with its own code.
            SignalOutcome::AlreadyShutDown => {}
        }
    });
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        // The lock covers request handling only, not the reply write: a stalled stdout reader
        // must not burn the signal thread's bound.
        let reply = shared.lock().handle_line(&line);
        if let Some(reply) = reply
            && (writeln!(out, "{reply}").is_err() || out.flush().is_err())
        {
            break;
        }
    }
    // Held through exit: a signal arriving now finds the shutdown done and changes nothing.
    let _guard = shared.finish_at_eof(Server::finish);
    std::process::exit(0);
}
