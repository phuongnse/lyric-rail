use std::{ffi::OsStr, os::windows::ffi::OsStrExt, path::Path, time::Duration};

use rand::{RngCore, rngs::OsRng};
use windows::{
    Win32::{
        Foundation::{
            CloseHandle, ERROR_IO_PENDING, GENERIC_READ, GENERIC_WRITE, HANDLE, RPC_E_TOO_LATE,
        },
        Storage::FileSystem::{
            CreateFileW, FILE_FLAG_OVERLAPPED, FILE_SHARE_NONE, OPEN_EXISTING, ReadFile, WriteFile,
        },
        System::{
            Com::{
                CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx,
                CoInitializeSecurity, CoSetProxyBlanket, CoUninitialize, EOAC_NONE,
                RPC_C_AUTHN_LEVEL_CALL, RPC_C_IMP_LEVEL_IMPERSONATE,
            },
            IO::{CancelIoEx, GetOverlappedResultEx, OVERLAPPED},
            Pipes::{
                GetNamedPipeServerProcessId, PIPE_READMODE_MESSAGE, SetNamedPipeHandleState,
                WaitNamedPipeW,
            },
            Rpc::{RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE},
            Threading::CreateEventW,
            Variant::{VARIANT, VariantClear, VariantToUInt32},
            Wmi::{
                IWbemClassObject, IWbemLocator, WBEM_E_ACCESS_DENIED, WBEM_FLAG_FORWARD_ONLY,
                WBEM_FLAG_RETURN_IMMEDIATELY, WbemLocator,
            },
        },
    },
    core::{BSTR, HRESULT, PCWSTR},
};
use windows_service::{
    service::{ServiceAccess, ServiceState, ServiceType},
    service_manager::{ServiceManager, ServiceManagerAccess},
};

use crate::{
    BROKER_PIPE_NAME, BROKER_REQUEST_LEN, BROKER_RESPONSE_LEN, BROKER_SERVICE_NAME,
    BitLockerStatus, BrokerEvidence, BrokerRequest, VolumeProtectionState, VolumeSecurityError,
    VolumeSecurityEvidence, decode_response, encode_request, resolve_drive_letter, response_status,
};

struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        // SAFETY: this type is constructed only from owned Win32 handles.
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

struct ComApartment;

impl Drop for ComApartment {
    fn drop(&mut self) {
        // SAFETY: the guard is created only after this thread initializes COM
        // and is always dropped on that same thread.
        unsafe { CoUninitialize() };
    }
}

fn validate_drive(drive: &str) -> Result<u8, VolumeSecurityError> {
    let bytes = drive.as_bytes();
    if bytes.len() != 2 || !bytes[0].is_ascii_alphabetic() || bytes[1] != b':' {
        return Err(VolumeSecurityError::UnsupportedVolume);
    }
    Ok(bytes[0].to_ascii_uppercase())
}

pub fn inspect_encrypted_volume(
    path: &Path,
    broker_timeout: Duration,
) -> Result<VolumeSecurityEvidence, VolumeSecurityError> {
    let (_, drive) = resolve_drive_letter(path)?;
    let (status, source) = match query_bitlocker_wmi(&drive) {
        Ok(status) => (
            status,
            "Verified directly with the native BitLocker provider.".to_owned(),
        ),
        Err(VolumeSecurityError::WmiAccessDenied) => {
            let evidence = query_broker(&drive, broker_timeout)?;
            (
                evidence.status,
                format!(
                    "Verified through the SCM-authenticated LyricRail Volume Broker (service PID {}).",
                    evidence.service_process_id
                ),
            )
        }
        Err(error) => return Err(error),
    };
    let state = status.state();
    let detail = match state {
        VolumeProtectionState::Protected => {
            format!("BitLocker protection is on and the volume is fully encrypted. {source}")
        }
        VolumeProtectionState::Unprotected => format!(
            "BitLocker does not report full active protection (ProtectionStatus={}, ConversionStatus={}). {source}",
            status.protection_status, status.conversion_status
        ),
        VolumeProtectionState::Unknown => format!(
            "BitLocker returned an unsupported status combination (ProtectionStatus={}, ConversionStatus={}). {source}",
            status.protection_status, status.conversion_status
        ),
    };
    Ok(VolumeSecurityEvidence {
        state,
        volume: Some(drive),
        detail,
    })
}

pub fn query_bitlocker_wmi(drive: &str) -> Result<BitLockerStatus, VolumeSecurityError> {
    validate_drive(drive)?;

    // SAFETY: this function is called on a dedicated worker thread. All COM
    // interfaces remain on it and are released before the apartment guard.
    unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) }
        .ok()
        .map_err(|error| VolumeSecurityError::Wmi(format!("CoInitializeEx failed: {error}")))?;
    let _apartment = ComApartment;

    // COM security is process-wide. RPC_E_TOO_LATE means another component
    // initialized it first; the per-proxy blanket below still pins the WMI call
    // to Windows authentication and the intended impersonation level.
    let security = unsafe {
        CoInitializeSecurity(
            None,
            -1,
            None,
            None,
            RPC_C_AUTHN_LEVEL_CALL,
            RPC_C_IMP_LEVEL_IMPERSONATE,
            None,
            EOAC_NONE,
            None,
        )
    };
    if let Err(error) = security
        && error.code() != RPC_E_TOO_LATE
    {
        return Err(VolumeSecurityError::Wmi(format!(
            "CoInitializeSecurity failed: {error}"
        )));
    }

    let locator: IWbemLocator = unsafe {
        CoCreateInstance(&WbemLocator, None, CLSCTX_INPROC_SERVER).map_err(|error| {
            VolumeSecurityError::Wmi(format!("cannot create WMI locator: {error}"))
        })?
    };
    let empty = BSTR::new();
    let services = unsafe {
        locator
            .ConnectServer(
                &BSTR::from("ROOT\\CIMV2\\Security\\MicrosoftVolumeEncryption"),
                &empty,
                &empty,
                &empty,
                0,
                &empty,
                None,
            )
            .map_err(|error| {
                if error.code().0 == WBEM_E_ACCESS_DENIED.0 {
                    VolumeSecurityError::WmiAccessDenied
                } else {
                    VolumeSecurityError::Wmi(format!(
                        "cannot connect to BitLocker provider: {error}"
                    ))
                }
            })?
    };
    unsafe {
        CoSetProxyBlanket(
            &services,
            RPC_C_AUTHN_WINNT,
            RPC_C_AUTHZ_NONE,
            PCWSTR::null(),
            RPC_C_AUTHN_LEVEL_CALL,
            RPC_C_IMP_LEVEL_IMPERSONATE,
            None,
            EOAC_NONE,
        )
        .map_err(|error| {
            VolumeSecurityError::Wmi(format!("cannot secure BitLocker proxy: {error}"))
        })?;
    }

    let query = BSTR::from(format!(
        "SELECT ProtectionStatus, ConversionStatus FROM Win32_EncryptableVolume WHERE DriveLetter = '{drive}'"
    ));
    let enumerator = unsafe {
        services
            .ExecQuery(
                &BSTR::from("WQL"),
                &query,
                WBEM_FLAG_FORWARD_ONLY | WBEM_FLAG_RETURN_IMMEDIATELY,
                None,
            )
            .map_err(|error| {
                if error.code().0 == WBEM_E_ACCESS_DENIED.0 {
                    VolumeSecurityError::WmiAccessDenied
                } else {
                    VolumeSecurityError::Wmi(format!("query failed: {error}"))
                }
            })?
    };
    let mut objects: [Option<IWbemClassObject>; 1] = [None];
    let mut returned = 0u32;
    unsafe { enumerator.Next(5_000, &mut objects, &mut returned) }
        .ok()
        .map_err(|error| {
            if error.code().0 == WBEM_E_ACCESS_DENIED.0 {
                VolumeSecurityError::WmiAccessDenied
            } else {
                VolumeSecurityError::Wmi(format!("enumeration failed: {error}"))
            }
        })?;
    if returned != 1 {
        return Err(VolumeSecurityError::Wmi(format!(
            "no encryptable volume was returned for {drive}"
        )));
    }
    let object = objects[0].take().ok_or_else(|| {
        VolumeSecurityError::Wmi("provider returned an empty volume object".to_owned())
    })?;

    unsafe fn property_u32(
        object: &IWbemClassObject,
        name: PCWSTR,
        label: &str,
    ) -> Result<u32, VolumeSecurityError> {
        let mut value = VARIANT::default();
        unsafe {
            object
                .Get(name, 0, &mut value, None, None)
                .map_err(|error| {
                    VolumeSecurityError::Wmi(format!("cannot read {label}: {error}"))
                })?;
        }
        let converted = unsafe { VariantToUInt32(&raw const value) }.map_err(|error| {
            VolumeSecurityError::Wmi(format!("{label} is not an unsigned integer: {error}"))
        });
        let cleared = unsafe { VariantClear(&raw mut value) }.map_err(|error| {
            VolumeSecurityError::Wmi(format!("cannot clear {label} value: {error}"))
        });
        let result = converted?;
        cleared?;
        Ok(result)
    }

    let protection_status = unsafe {
        property_u32(
            &object,
            windows::core::w!("ProtectionStatus"),
            "ProtectionStatus",
        )?
    };
    let conversion_status = unsafe {
        property_u32(
            &object,
            windows::core::w!("ConversionStatus"),
            "ConversionStatus",
        )?
    };
    Ok(BitLockerStatus {
        protection_status,
        conversion_status,
    })
}

pub fn query_broker(drive: &str, timeout: Duration) -> Result<BrokerEvidence, VolumeSecurityError> {
    let drive_letter = validate_drive(drive)?;
    let timeout_ms = u32::try_from(timeout.as_millis().clamp(1, u128::from(u32::MAX)))
        .expect("clamped duration fits u32");

    let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)
        .map_err(|error| VolumeSecurityError::BrokerUnavailable(error.to_string()))?;
    let service = manager
        .open_service(BROKER_SERVICE_NAME, ServiceAccess::QUERY_STATUS)
        .map_err(|error| VolumeSecurityError::BrokerUnavailable(error.to_string()))?;
    let status = service
        .query_status()
        .map_err(|error| VolumeSecurityError::BrokerUnavailable(error.to_string()))?;
    if status.current_state != ServiceState::Running {
        return Err(VolumeSecurityError::BrokerUnavailable(format!(
            "service state is {:?}",
            status.current_state
        )));
    }
    if status.service_type != ServiceType::OWN_PROCESS {
        return Err(VolumeSecurityError::BrokerIdentity(format!(
            "service type {:?} is not an isolated own-process service",
            status.service_type
        )));
    }
    let service_pid = status.process_id.filter(|pid| *pid != 0).ok_or_else(|| {
        VolumeSecurityError::BrokerIdentity("SCM returned no running service PID".to_owned())
    })?;

    let pipe_name = wide_null(BROKER_PIPE_NAME);
    let available = unsafe { WaitNamedPipeW(PCWSTR(pipe_name.as_ptr()), timeout_ms) };
    if !available.as_bool() {
        return Err(VolumeSecurityError::BrokerUnavailable(
            windows::core::Error::from_win32().to_string(),
        ));
    }
    let handle = unsafe {
        CreateFileW(
            PCWSTR(pipe_name.as_ptr()),
            GENERIC_READ.0 | GENERIC_WRITE.0,
            FILE_SHARE_NONE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
    }
    .map(OwnedHandle)
    .map_err(|error| VolumeSecurityError::BrokerUnavailable(error.to_string()))?;

    let mut pipe_pid = 0u32;
    unsafe { GetNamedPipeServerProcessId(handle.0, &mut pipe_pid) }
        .map_err(|error| VolumeSecurityError::BrokerIdentity(error.to_string()))?;
    if pipe_pid != service_pid {
        return Err(VolumeSecurityError::BrokerIdentity(format!(
            "pipe server PID {pipe_pid} does not match SCM service PID {service_pid}"
        )));
    }

    let mode = PIPE_READMODE_MESSAGE;
    unsafe { SetNamedPipeHandleState(handle.0, Some(&mode), None, None) }
        .map_err(|error| VolumeSecurityError::BrokerProtocol(error.to_string()))?;

    let mut nonce = [0u8; 16];
    OsRng.fill_bytes(&mut nonce);
    let request = encode_request(&BrokerRequest {
        drive_letter,
        nonce,
    })
    .map_err(VolumeSecurityError::BrokerProtocol)?;
    write_exact_overlapped(handle.0, &request, timeout_ms)?;

    let mut response_bytes = [0u8; BROKER_RESPONSE_LEN];
    read_exact_overlapped(handle.0, &mut response_bytes, timeout_ms)?;
    let response =
        decode_response(&response_bytes, &nonce).map_err(VolumeSecurityError::BrokerProtocol)?;
    let status = response_status(response)?;
    Ok(BrokerEvidence {
        service_process_id: service_pid,
        status,
    })
}

fn wide_null(value: &str) -> Vec<u16> {
    OsStr::new(value).encode_wide().chain(Some(0)).collect()
}

fn write_exact_overlapped(
    handle: HANDLE,
    bytes: &[u8; BROKER_REQUEST_LEN],
    timeout_ms: u32,
) -> Result<(), VolumeSecurityError> {
    let event = unsafe { CreateEventW(None, true, false, None) }
        .map(OwnedHandle)
        .map_err(|error| VolumeSecurityError::BrokerProtocol(error.to_string()))?;
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

fn read_exact_overlapped(
    handle: HANDLE,
    bytes: &mut [u8; BROKER_RESPONSE_LEN],
    timeout_ms: u32,
) -> Result<(), VolumeSecurityError> {
    let event = unsafe { CreateEventW(None, true, false, None) }
        .map(OwnedHandle)
        .map_err(|error| VolumeSecurityError::BrokerProtocol(error.to_string()))?;
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

fn complete_overlapped(
    handle: HANDLE,
    overlapped: &mut OVERLAPPED,
    timeout_ms: u32,
    started: windows::core::Result<()>,
    expected: usize,
    operation: &str,
) -> Result<(), VolumeSecurityError> {
    if let Err(error) = &started
        && error.code() != HRESULT::from_win32(ERROR_IO_PENDING.0)
    {
        return Err(VolumeSecurityError::BrokerProtocol(format!(
            "pipe {operation} could not start: {error}"
        )));
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
        // SAFETY: the OVERLAPPED remains alive through cancellation and handle
        // closure; failure to cancel is secondary to the original fail-closed error.
        unsafe {
            let _ = CancelIoEx(handle, Some(&raw const *overlapped));
        }
        return Err(VolumeSecurityError::BrokerProtocol(format!(
            "pipe {operation} failed or timed out: {error}"
        )));
    }
    if transferred as usize != expected {
        return Err(VolumeSecurityError::BrokerProtocol(format!(
            "pipe {operation} transferred {transferred} bytes; expected {expected}"
        )));
    }
    Ok(())
}
