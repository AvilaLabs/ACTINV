//! Worker polling protocol, shared verbatim with the verification harnesses.
//! OS process operations stay behind this narrow interface so model tests can
//! explore completion/cancellation order without launching a test executable.
use std::sync::mpsc::TryRecvError;

#[derive(Clone, Copy)]
pub enum CancellationCheck {
    WhileRunning,
    AfterExit,
}

pub fn cancellation_requested(result: Result<(), TryRecvError>, check: CancellationCheck) -> bool {
    match result {
        Ok(()) => true,
        Err(TryRecvError::Disconnected) => matches!(check, CancellationCheck::WhileRunning),
        Err(TryRecvError::Empty) => false,
    }
}

pub trait Process {
    type Status;
    type Error;
    fn try_wait(&mut self) -> Result<Option<Self::Status>, Self::Error>;
    fn terminate_and_wait(&mut self) -> Result<Self::Status, Self::Error>;
}

impl Process for std::process::Child {
    type Status = std::process::ExitStatus;
    type Error = std::io::Error;

    fn try_wait(&mut self) -> Result<Option<Self::Status>, Self::Error> {
        std::process::Child::try_wait(self)
    }

    fn terminate_and_wait(&mut self) -> Result<Self::Status, Self::Error> {
        let _ = self.kill();
        self.wait()
    }
}

pub fn wait_for_exit<P: Process>(
    process: &mut P,
    mut cancel: impl FnMut(CancellationCheck) -> bool,
    mut idle: impl FnMut(),
) -> (Result<P::Status, P::Error>, bool) {
    let mut cancelled = false;
    let status = loop {
        if cancel(CancellationCheck::WhileRunning) {
            cancelled = true;
            break process.terminate_and_wait();
        }
        match process.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) => idle(),
            Err(error) => {
                let _ = process.terminate_and_wait();
                break Err(error);
            }
        }
    };
    // Preserve the production rule: a queued cancel wins the final exit race.
    if !cancelled && cancel(CancellationCheck::AfterExit) {
        cancelled = true;
    }
    (status, cancelled)
}
