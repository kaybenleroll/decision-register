//! SIGHUP, SIGINT and SIGTERM run the same shutdown as end of input.
//!
//! No handler is installed. Each signal is first reset to its default disposition (so a parent's
//! `nohup`-style ignore cannot swallow it), then blocked in the calling thread, which every thread
//! spawned later inherits. One dedicated thread takes the signals with `sigwait`, so the shutdown
//! runs as ordinary code: no async-signal-safety rules, no interrupted `read`. Linux only (like
//! claim.rs).

use std::mem::MaybeUninit;

const HANDLED: [libc::c_int; 3] = [libc::SIGHUP, libc::SIGINT, libc::SIGTERM];

fn handled_set() -> libc::sigset_t {
    let mut set = MaybeUninit::<libc::sigset_t>::uninit();
    // SAFETY: `sigemptyset` initialises the set; `sigaddset` gets a valid set and valid signals.
    unsafe {
        libc::sigemptyset(set.as_mut_ptr());
        for sig in HANDLED {
            libc::sigaddset(set.as_mut_ptr(), sig);
        }
        set.assume_init()
    }
}

/// Resets the three signals to `SIG_DFL` and blocks them in the calling thread. Call first in
/// `main`, before any thread is spawned.
pub fn reset_and_block() {
    let set = handled_set();
    // SAFETY: plain libc calls on valid values; the old mask is not wanted.
    unsafe {
        for sig in HANDLED {
            libc::signal(sig, libc::SIG_DFL);
        }
        libc::pthread_sigmask(libc::SIG_BLOCK, &set, std::ptr::null_mut());
    }
}

/// Runs `on_signal(signo)` on a new thread when the first of the blocked signals arrives.
pub fn spawn_waiter(on_signal: impl FnOnce(i32) + Send + 'static) {
    let set = handled_set();
    std::thread::spawn(move || {
        let mut sig: libc::c_int = 0;
        // SAFETY: `set` is initialised and `sig` is a valid out-pointer.
        if unsafe { libc::sigwait(&set, &mut sig) } == 0 {
            on_signal(sig);
        }
    });
}
