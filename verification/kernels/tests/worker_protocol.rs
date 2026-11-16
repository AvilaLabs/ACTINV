use actinv_verification_kernels::worker_wait::{self, CancellationCheck, Process};
use std::sync::mpsc::TryRecvError;

struct Fake {
    complete: bool,
    fail: bool,
    terminated: usize,
}

impl Process for Fake {
    type Status = ();
    type Error = &'static str;
    fn try_wait(&mut self) -> Result<Option<()>, Self::Error> {
        if self.fail {
            Err("poll failed")
        } else {
            Ok(self.complete.then_some(()))
        }
    }
    fn terminate_and_wait(&mut self) -> Result<(), Self::Error> {
        self.terminated += 1;
        Ok(())
    }
}

#[test]
fn cancellation_at_completion_has_priority() {
    let mut child = Fake {
        complete: true,
        fail: false,
        terminated: 0,
    };
    let (result, cancelled) = worker_wait::wait_for_exit(
        &mut child,
        |phase| matches!(phase, CancellationCheck::AfterExit),
        || panic!("already exited"),
    );
    assert!(result.is_ok());
    assert!(cancelled);
    assert_eq!(child.terminated, 0);
}

#[test]
fn cancellation_reaps_before_returning_and_poll_errors_preserve_the_error() {
    for cancel in [true, false] {
        let mut child = Fake {
            complete: false,
            fail: true,
            terminated: 0,
        };
        let (result, cancelled) =
            worker_wait::wait_for_exit(&mut child, |_| cancel, || panic!("must terminate"));
        assert_eq!(child.terminated, 1);
        assert_eq!(cancelled, cancel);
        assert_eq!(result, if cancel { Ok(()) } else { Err("poll failed") });
    }
}

#[test]
fn disconnection_only_cancels_a_running_worker() {
    assert!(worker_wait::cancellation_requested(
        Err(TryRecvError::Disconnected),
        CancellationCheck::WhileRunning
    ));
    assert!(!worker_wait::cancellation_requested(
        Err(TryRecvError::Disconnected),
        CancellationCheck::AfterExit
    ));
    for phase in [
        CancellationCheck::WhileRunning,
        CancellationCheck::AfterExit,
    ] {
        assert!(worker_wait::cancellation_requested(Ok(()), phase));
        assert!(!worker_wait::cancellation_requested(
            Err(TryRecvError::Empty),
            phase
        ));
    }
}
