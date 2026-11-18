use std::io::{self, BufRead, Write};

use regspike_rs::protocol::Server;

fn main() {
    let stdin = io::stdin();
    let mut out = io::stdout().lock();
    let mut server = Server::new();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        if let Some(reply) = server.handle_line(&line)
            && (writeln!(out, "{reply}").is_err() || out.flush().is_err())
        {
            break;
        }
    }
}
