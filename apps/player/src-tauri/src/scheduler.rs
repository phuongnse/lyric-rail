use std::sync::{Condvar, Mutex};

const MAX_FOREGROUND_ACTIVE: usize = 8;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IoPriority {
    Playback,
    AlternateTrack,
    Background,
}

#[derive(Default)]
struct State {
    foreground_active: usize,
    foreground_waiting: usize,
    background_active: bool,
}

/// A small fairness gate. Background work owns at most one bounded block and
/// cannot start while playback/seek/alternate-track work is waiting.
#[derive(Default)]
pub struct PriorityScheduler {
    state: Mutex<State>,
    changed: Condvar,
}

pub struct PriorityPermit<'a> {
    scheduler: &'a PriorityScheduler,
    foreground: bool,
}

impl PriorityScheduler {
    pub fn acquire(&self, priority: IoPriority) -> Result<PriorityPermit<'_>, String> {
        let foreground = priority != IoPriority::Background;
        let mut state = self
            .state
            .lock()
            .map_err(|_| "I/O scheduler lock is poisoned".to_string())?;
        if foreground {
            state.foreground_waiting += 1;
            state = self
                .changed
                .wait_while(state, |state| {
                    state.background_active || state.foreground_active >= MAX_FOREGROUND_ACTIVE
                })
                .map_err(|_| "I/O scheduler wait failed".to_string())?;
            state.foreground_waiting -= 1;
            state.foreground_active += 1;
        } else {
            state = self
                .changed
                .wait_while(state, |state| {
                    state.background_active
                        || state.foreground_active > 0
                        || state.foreground_waiting > 0
                })
                .map_err(|_| "I/O scheduler wait failed".to_string())?;
            state.background_active = true;
        }
        drop(state);
        Ok(PriorityPermit {
            scheduler: self,
            foreground,
        })
    }
}

impl Drop for PriorityPermit<'_> {
    fn drop(&mut self) {
        if let Ok(mut state) = self.scheduler.state.lock() {
            if self.foreground {
                state.foreground_active = state.foreground_active.saturating_sub(1);
            } else {
                state.background_active = false;
            }
            self.scheduler.changed.notify_all();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{IoPriority, MAX_FOREGROUND_ACTIVE, PriorityScheduler};
    use std::{
        sync::{
            Arc, Barrier, Mutex,
            atomic::{AtomicBool, Ordering},
        },
        thread,
    };

    #[test]
    fn waiting_playback_precedes_the_next_background_block() {
        let scheduler = Arc::new(PriorityScheduler::default());
        let first_background = scheduler.acquire(IoPriority::Background).unwrap();
        let ready = Arc::new(Barrier::new(2));
        let order = Arc::new(Mutex::new(Vec::new()));

        let playback = {
            let scheduler = scheduler.clone();
            let ready = ready.clone();
            let order = order.clone();
            thread::spawn(move || {
                ready.wait();
                let _permit = scheduler.acquire(IoPriority::Playback).unwrap();
                order.lock().unwrap().push("playback");
            })
        };
        ready.wait();
        while scheduler.state.lock().unwrap().foreground_waiting == 0 {
            thread::yield_now();
        }
        drop(first_background);
        let _next_background = scheduler.acquire(IoPriority::Background).unwrap();
        order.lock().unwrap().push("background");
        drop(_next_background);
        playback.join().unwrap();
        assert_eq!(*order.lock().unwrap(), vec!["playback", "background"]);
    }

    #[test]
    fn foreground_concurrency_is_bounded() {
        let scheduler = Arc::new(PriorityScheduler::default());
        let permits = (0..MAX_FOREGROUND_ACTIVE)
            .map(|_| scheduler.acquire(IoPriority::Playback).unwrap())
            .collect::<Vec<_>>();
        let acquired = Arc::new(AtomicBool::new(false));
        let waiter = {
            let scheduler = scheduler.clone();
            let acquired = acquired.clone();
            thread::spawn(move || {
                let _permit = scheduler.acquire(IoPriority::AlternateTrack).unwrap();
                acquired.store(true, Ordering::Release);
            })
        };
        while scheduler.state.lock().unwrap().foreground_waiting == 0 {
            thread::yield_now();
        }
        assert!(!acquired.load(Ordering::Acquire));
        drop(permits);
        waiter.join().unwrap();
        assert!(acquired.load(Ordering::Acquire));
    }
}
