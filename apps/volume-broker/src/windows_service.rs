use std::{
    ffi::OsStr,
    os::windows::ffi::OsStrExt,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use lrail_volume_security::{
    BROKER_PIPE_NAME, BROKER_REQUEST_LEN, BROKER_RESPONSE_LEN, BROKER_SERVICE_NAME, decode_request,
    encode_response, failure_response, query_bitlocker_wmi, success_response,
};
use windows::{
    Win32::{
        Foundation::{
            CloseHandle, ERROR_IO_PENDING, ERROR_PIPE_CONNECTED, HANDLE, HLOCAL, LocalFree,
            WAIT_OBJECT_0, WAIT_TIMEOUT,
        },
        Security::{
            Authorization::{
                ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1,
            },
            PSECURITY_DESCRIPTOR, SECURITY_ATTRIBUTES,
        },
        Storage::FileSystem::{
            FILE_FLAG_FIRST_PIPE_INSTANCE, FILE_FLAG_OVERLAPPED, PIPE_ACCESS_DUPLEX, ReadFile,
            WriteFile,
        },
        System::{
            IO::{CancelIoEx, GetOverlappedResultEx, OVERLAPPED},
            Pipes::{
                ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_READMODE_MESSAGE,
                PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_MESSAGE, PIPE_WAIT,
            },
            Threading::{CreateEventW, WaitForSingleObject},
        },
    },
    core::{BOOL, HRESULT, PCWSTR},
};
use windows_service::{
    define_windows_service,
    service::{
        ServiceControl, ServiceControlAccept, ServiceExitCode, ServiceState, ServiceStatus,
        ServiceType,
    },
    service_control_handler::{self, ServiceControlHandlerResult},
    service_dispatcher,
};

const PIPE_IO_TIMEOUT_MS: u32 = 7_000;

define_windows_service!(ffi_service_main, service_main);

pub fn run() -> windows_service::Result<()> {
    service_dispatcher::start(BROKER_SERVICE_NAME, ffi_service_main)
}

fn service_main(_arguments: Vec<std::ffi::OsString>) {
    let _ = run_service();
}

fn run_service() -> windows_service::Result<()> {
    let stopping = Arc::new(AtomicBool::new(false));
    let stopping_for_handler = Arc::clone(&stopping);
    let event_handler = move |event| -> ServiceControlHandlerResult {
        match event {
            ServiceControl::Stop | ServiceControl::Shutdown => {
                stopping_for_handler.store(true, Ordering::Release);
                ServiceControlHandlerResult::NoError
            }
            ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
            _ => ServiceControlHandlerResult::NotImplemented,
        }
    };
    let status_handle = service_control_handler::register(BROKER_SERVICE_NAME, event_handler)?;
    status_handle.set_service_status(ServiceStatus {
        service_type: ServiceType::OWN_PROCESS,
        current_state: ServiceState::Running,
        controls_accepted: ServiceControlAccept::STOP | ServiceControlAccept::SHUTDOWN,
        exit_code: ServiceExitCode::Win32(0),
        checkpoint: 0,
        wait_hint: Duration::default(),
        process_id: None,
    })?;

    let server_result = serve_until_stopped(&stopping);
    status_handle.set_service_status(ServiceStatus {
        service_type: ServiceType::OWN_PROCESS,
        current_state: ServiceState::Stopped,
        controls_accepted: ServiceControlAccept::empty(),
        exit_code: if server_result.is_ok() {
            ServiceExitCode::Win32(0)
        } else {
            ServiceExitCode::ServiceSpecific(1)
        },
        checkpoint: 0,
        wait_hint: Duration::default(),
        process_id: None,
    })?;
    server_result.map_err(|error| windows_service::Error::Winapi(std::io::Error::other(error)))
}

struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        // SAFETY: this type owns every handle with which it is constructed.
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

struct OwnedSecurityDescriptor(PSECURITY_DESCRIPTOR);

impl Drop for OwnedSecurityDescriptor {
    fn drop(&mut self) {
        // SAFETY: ConvertStringSecurityDescriptor allocates this descriptor with
        // LocalAlloc and ownership remains with this guard.
        unsafe {
            let _ = LocalFree(Some(HLOCAL(self.0.0)));
        }
    }
}

fn serve_until_stopped(stopping: &AtomicBool) -> Result<(), String> {
    let descriptor = create_pipe_security_descriptor()?;
    while !stopping.load(Ordering::Acquire) {
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0.0,
            bInheritHandle: BOOL(0),
        };
        let pipe_name = wide_null(BROKER_PIPE_NAME);
        let pipe = unsafe {
            CreateNamedPipeW(
                PCWSTR(pipe_name.as_ptr()),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                BROKER_RESPONSE_LEN as u32,
                BROKER_REQUEST_LEN as u32,
                PIPE_IO_TIMEOUT_MS,
                Some(&raw const attributes),
            )
        };
        if pipe.is_invalid() {
            return Err(format!(
                "cannot create the authenticated local pipe: {}",
                windows::core::Error::from_win32()
            ));
        }
        let pipe = OwnedHandle(pipe);
        if !wait_for_client(pipe.0, stopping)? {
            break;
        }
        let _ = handle_one_request(pipe.0);
        unsafe {
            let _ = DisconnectNamedPipe(pipe.0);
        }
    }
    Ok(())
}

fn create_pipe_security_descriptor() -> Result<OwnedSecurityDescriptor, String> {
    // Protected DACL: full access for SYSTEM/Admins, read+write for local
    // authenticated users. Medium mandatory label blocks low-integrity writers.
    // PIPE_REJECT_REMOTE_CLIENTS independently rejects network clients.
    let sddl = wide_null("D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;AU)S:(ML;;NW;;;ME)");
    let mut descriptor = PSECURITY_DESCRIPTOR::default();
    unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            PCWSTR(sddl.as_ptr()),
            SDDL_REVISION_1,
            &mut descriptor,
            None,
        )
    }
    .map_err(|error| format!("cannot construct pipe security descriptor: {error}"))?;
    Ok(OwnedSecurityDescriptor(descriptor))
}

fn wait_for_client(pipe: HANDLE, stopping: &AtomicBool) -> Result<bool, String> {
    let event = unsafe { CreateEventW(None, true, false, None) }
        .map(OwnedHandle)
        .map_err(|error| format!("cannot create connection event: {error}"))?;
    let mut overlapped = OVERLAPPED {
        hEvent: event.0,
        ..Default::default()
    };
    let started = unsafe { ConnectNamedPipe(pipe, Some(&raw mut overlapped)) };
    match started {
        Ok(()) => return Ok(true),
        Err(error) if error.code() == HRESULT::from_win32(ERROR_PIPE_CONNECTED.0) => {
            return Ok(true);
        }
        Err(error) if error.code() == HRESULT::from_win32(ERROR_IO_PENDING.0) => {}
        Err(error) => return Err(format!("cannot await a local client: {error}")),
    }

    loop {
        if stopping.load(Ordering::Acquire) {
            unsafe {
                let _ = CancelIoEx(pipe, Some(&raw const overlapped));
            }
            return Ok(false);
        }
        let wait = unsafe { WaitForSingleObject(event.0, 200) };
        if wait == WAIT_OBJECT_0 {
            let mut transferred = 0u32;
            unsafe {
                GetOverlappedResultEx(pipe, &raw const overlapped, &mut transferred, 0, false)
            }
            .map_err(|error| format!("named-pipe connection did not complete: {error}"))?;
            return Ok(true);
        }
        if wait != WAIT_TIMEOUT {
            return Err(format!("unexpected connection wait result: {wait:?}"));
        }
    }
}

fn handle_one_request(pipe: HANDLE) -> Result<(), String> {
    let mut request_bytes = [0u8; BROKER_REQUEST_LEN];
    read_exact_overlapped(pipe, &mut request_bytes, PIPE_IO_TIMEOUT_MS)?;
    let request = decode_request(&request_bytes)?;
    let drive = format!("{}:", char::from(request.drive_letter));
    let response = match query_bitlocker_wmi(&drive) {
        Ok(status) => success_response(request.nonce, status),
        Err(_) => failure_response(request.nonce),
    };
    let encoded = encode_response(&response);
    write_exact_overlapped(pipe, &encoded, PIPE_IO_TIMEOUT_MS)
}

fn read_exact_overlapped(
    handle: HANDLE,
    bytes: &mut [u8; BROKER_REQUEST_LEN],
    timeout_ms: u32,
) -> Result<(), String> {
    let event = unsafe { CreateEventW(None, true, false, None) }
        .map(OwnedHandle)
        .map_err(|error| format!("cannot create read event: {error}"))?;
    let mut overlapped = OVERLAPPED {
        hEvent: event.0,
        ..Default::default()
    };
    let started = unsafe { ReadFile(handle, Some(bytes), None, Some(&raw mut overlapped)) };
    complete_overlapped(
        handle,
        &mut overlapped,
        timeout_ms,
        started,
        bytes.len(),
        "read",
    )
}

fn write_exact_overlapped(
    handle: HANDLE,
    bytes: &[u8; BROKER_RESPONSE_LEN],
    timeout_ms: u32,
) -> Result<(), String> {
    let event = unsafe { CreateEventW(None, true, false, None) }
        .map(OwnedHandle)
        .map_err(|error| format!("cannot create write event: {error}"))?;
    let mut overlapped = OVERLAPPED {
        hEvent: event.0,
        ..Default::default()
    };
    let started = unsafe { WriteFile(handle, Some(bytes), None, Some(&raw mut overlapped)) };
    complete_overlapped(
        handle,
        &mut overlapped,
        timeout_ms,
        started,
        bytes.len(),
        "write",
    )
}

fn complete_overlapped(
    handle: HANDLE,
    overlapped: &mut OVERLAPPED,
    timeout_ms: u32,
    started: windows::core::Result<()>,
    expected: usize,
    operation: &str,
) -> Result<(), String> {
    if let Err(error) = &started
        && error.code() != HRESULT::from_win32(ERROR_IO_PENDING.0)
    {
        return Err(format!("pipe {operation} could not start: {error}"));
    }
    let mut transferred = 0u32;
    let completed = unsafe {
        GetOverlappedResultEx(
            handle,
            &raw const *overlapped,
            &mut transferred,
            timeout_ms,
            false,
        )
    };
    if let Err(error) = completed {
        unsafe {
            let _ = CancelIoEx(handle, Some(&raw const *overlapped));
        }
        return Err(format!("pipe {operation} failed or timed out: {error}"));
    }
    if transferred as usize != expected {
        return Err(format!(
            "pipe {operation} transferred {transferred} bytes; expected {expected}"
        ));
    }
    Ok(())
}

fn wide_null(value: &str) -> Vec<u16> {
    OsStr::new(value).encode_wide().chain(Some(0)).collect()
}
