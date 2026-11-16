use actinv_verification_kernels::worker_wait::{self, Process};
use loom::{
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc,
    },
    thread,
};

struct FakeChild {
    completed: Arc<AtomicBool>,
    reaped: bool,
    killed: usize,
}

impl Process for FakeChild {
    type Status = ();
    type Error = ();
    fn try_wait(&mut self) -> Result<Option<()>, ()> {
        if self.completed.load(Ordering::Acquire) {
            self.reaped = true;
            Ok(Some(()))
        } else {
            Ok(None)
        }
    }
    fn terminate_and_wait(&mut self) -> Result<(), ()> {
        self.killed += 1;
        self.reaped = true;
        Ok(())
    }
}

#[test]
fn queued_cancel_wins_even_when_child_completes_between_polls() {
    loom::model(|| {
        let (tx, rx) = mpsc::channel();
        let completed = Arc::new(AtomicBool::new(false));
        let done = completed.clone();
        let producer = thread::spawn(move || {
            tx.send(()).unwrap();
            done.store(true, Ordering::Release);
        });
        let mut child = FakeChild {
            completed,
            reaped: false,
            killed: 0,
        };
        let (result, cancelled) = worker_wait::wait_for_exit(
            &mut child,
            |phase| worker_wait::cancellation_requested(rx.try_recv(), phase),
            thread::yield_now,
        );
        producer.join().unwrap();
        assert!(result.is_ok());
        assert!(cancelled);
        assert!(child.reaped);
        assert!(child.killed <= 1);
    });
}

#[test]
fn normal_completion_is_reaped_without_killing() {
    loom::model(|| {
        let (_tx, rx) = mpsc::channel();
        let completed = Arc::new(AtomicBool::new(false));
        let done = completed.clone();
        let producer = thread::spawn(move || done.store(true, Ordering::Release));
        let mut child = FakeChild {
            completed,
            reaped: false,
            killed: 0,
        };
        let (result, cancelled) = worker_wait::wait_for_exit(
            &mut child,
            |phase| worker_wait::cancellation_requested(rx.try_recv(), phase),
            thread::yield_now,
        );
        producer.join().unwrap();
        assert!(result.is_ok());
        assert!(!cancelled);
        assert!(child.reaped);
        assert_eq!(child.killed, 0);
    });
}
