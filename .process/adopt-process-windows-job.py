# Managed by engineering-process; do not edit.
"""Run one command in a Windows kill-on-close Job Object.

This private helper is launched by the environment executor. The Job Object is
attached through the process creation attribute list so the target is contained
before its first instruction can run, including if this helper is terminated while
CreateProcessW is in progress.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import ntpath
import os
import subprocess
import sys
import time
from typing import Any


CREATE_NEW_PROCESS_GROUP = 0x00000200
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
INFINITE = 0xFFFFFFFF
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
STARTF_USESTDHANDLES = 0x00000100
STD_INPUT_HANDLE = wintypes.DWORD(-10).value
STD_OUTPUT_HANDLE = wintypes.DWORD(-11).value
STD_ERROR_HANDLE = wintypes.DWORD(-12).value
WAIT_FAILED = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
CLEANUP_GRACE_MILLISECONDS = 5_000
NATURAL_DRAIN_GRACE_MILLISECONDS = 250


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _last_error(label: str) -> OSError:
    get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
    error_code = get_last_error()
    win_error = getattr(ctypes, "WinError", None)
    if win_error is not None:
        return win_error(error_code, label)
    return OSError(error_code, f"{label} failed")


def _configure_kernel32(kernel32: Any) -> None:
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _active_processes(kernel32: Any, job: int) -> int:
    accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned_length = wintypes.DWORD()
    if not kernel32.QueryInformationJobObject(
        job,
        JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        ctypes.byref(returned_length),
    ):
        raise _last_error("QueryInformationJobObject")
    return int(accounting.ActiveProcesses)


def _cleanup_process_and_job(
    kernel32: Any,
    job: int,
    process_info: PROCESS_INFORMATION,
    *,
    allow_natural_drain: bool,
) -> tuple[list[OSError], int | None]:
    errors: list[OSError] = []
    if process_info.hProcess:
        try:
            active_processes = _active_processes(kernel32, job)
        except OSError as error:
            errors.append(error)
            active_processes = None
        if allow_natural_drain and active_processes:
            drain_deadline = (
                time.monotonic() + NATURAL_DRAIN_GRACE_MILLISECONDS / 1000
            )
            while active_processes > 0 and time.monotonic() < drain_deadline:
                time.sleep(0.01)
                try:
                    active_processes = _active_processes(kernel32, job)
                except OSError as error:
                    errors.append(error)
                    active_processes = None
                    break
        active_processes_before_cleanup = active_processes

        if active_processes is None or active_processes > 0:
            if not kernel32.TerminateJobObject(job, 125):
                errors.append(_last_error("TerminateJobObject"))
            deadline = time.monotonic() + CLEANUP_GRACE_MILLISECONDS / 1000
            while True:
                try:
                    active_processes = _active_processes(kernel32, job)
                except OSError as error:
                    errors.append(error)
                    break
                if active_processes == 0:
                    break
                if time.monotonic() >= deadline:
                    errors.append(
                        OSError(
                            "Job Object retained active processes after bounded termination"
                        )
                    )
                    break
                time.sleep(0.01)

        wait_result = kernel32.WaitForSingleObject(
            process_info.hProcess, CLEANUP_GRACE_MILLISECONDS
        )
        if wait_result == WAIT_FAILED:
            errors.append(_last_error("WaitForSingleObject during cleanup"))
        elif wait_result == WAIT_TIMEOUT:
            errors.append(OSError("target process survived bounded Job Object cleanup"))
        elif wait_result != WAIT_OBJECT_0:
            errors.append(
                OSError(f"unexpected target cleanup wait result: {wait_result}")
            )
    return errors, (
        active_processes_before_cleanup if process_info.hProcess else None
    )


def _close_handle(kernel32: Any, handle: int, label: str) -> OSError | None:
    if handle and not kernel32.CloseHandle(handle):
        return _last_error(f"CloseHandle({label})")
    return None


def _run(
    application: str,
    command: list[str],
    *,
    kernel32: Any | None = None,
) -> int:
    if not ntpath.isabs(application) or ntpath.splitext(application)[1].casefold() != ".exe":
        raise OSError("Windows Job Object application must be an absolute .exe path")
    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _configure_kernel32(kernel32)

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _last_error("CreateJobObjectW")
    process_info = PROCESS_INFORMATION()
    attribute_list: ctypes.c_void_p | None = None
    attribute_list_initialized = False
    caught_error: BaseException | None = None
    exit_code: int | None = None
    try:
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise _last_error("SetInformationJobObject")

        attribute_list_size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(
            None, 1, 0, ctypes.byref(attribute_list_size)
        )
        if attribute_list_size.value == 0:
            raise _last_error("InitializeProcThreadAttributeList(size)")
        attribute_list_buffer = ctypes.create_string_buffer(attribute_list_size.value)
        attribute_list = ctypes.cast(attribute_list_buffer, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(
            attribute_list, 1, 0, ctypes.byref(attribute_list_size)
        ):
            raise _last_error("InitializeProcThreadAttributeList")
        attribute_list_initialized = True
        job_handles = (wintypes.HANDLE * 1)(job)
        if not kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            PROC_THREAD_ATTRIBUTE_JOB_LIST,
            ctypes.cast(job_handles, ctypes.c_void_p),
            ctypes.sizeof(job_handles),
            None,
            None,
        ):
            raise _last_error("UpdateProcThreadAttribute(JOB_LIST)")

        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        startup.StartupInfo.hStdOutput = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        startup.StartupInfo.hStdError = kernel32.GetStdHandle(STD_ERROR_HANDLE)
        startup.lpAttributeList = attribute_list
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        if not kernel32.CreateProcessW(
            application,
            command_line,
            None,
            None,
            True,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_NEW_PROCESS_GROUP,
            None,
            None,
            ctypes.cast(ctypes.byref(startup), ctypes.POINTER(STARTUPINFOW)),
            ctypes.byref(process_info),
        ):
            raise _last_error("CreateProcessW")
        wait_result = kernel32.WaitForSingleObject(process_info.hProcess, INFINITE)
        if wait_result == WAIT_FAILED:
            raise _last_error("WaitForSingleObject")
        if wait_result != WAIT_OBJECT_0:
            raise OSError(f"unexpected target wait result: {wait_result}")
        native_exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(
            process_info.hProcess, ctypes.byref(native_exit_code)
        ):
            raise _last_error("GetExitCodeProcess")
        exit_code = int(native_exit_code.value)
    except BaseException as error:
        caught_error = error
    finally:
        cleanup_errors, active_processes_before_cleanup = _cleanup_process_and_job(
            kernel32,
            job,
            process_info,
            allow_natural_drain=caught_error is None,
        )
        if attribute_list_initialized and attribute_list is not None:
            kernel32.DeleteProcThreadAttributeList(attribute_list)
        for handle, label in (
            (process_info.hThread, "thread"),
            (process_info.hProcess, "process"),
            (job, "job"),
        ):
            if error := _close_handle(kernel32, handle, label):
                cleanup_errors.append(error)

    if caught_error is not None:
        if cleanup_errors and isinstance(caught_error, Exception):
            details = "; ".join(str(error) for error in cleanup_errors)
            raise OSError(f"{caught_error}; cleanup failed: {details}") from caught_error
        if cleanup_errors:
            caught_error.add_note(
                "Windows Job Object cleanup failed: "
                + "; ".join(str(error) for error in cleanup_errors)
            )
        raise caught_error
    if cleanup_errors:
        raise OSError(
            "Windows Job Object cleanup failed: "
            + "; ".join(str(error) for error in cleanup_errors)
        )
    if active_processes_before_cleanup:
        raise OSError("target command left descendant processes; they were terminated")
    assert exit_code is not None
    return exit_code


def main() -> int:
    if os.name != "nt":
        print("Windows Job Object runner is only available on Windows", file=sys.stderr)
        return 125
    arguments = sys.argv[1:]
    if len(arguments) < 4 or arguments[0] != "--application" or arguments[2] != "--":
        print(
            "Windows Job Object runner requires --application ABSOLUTE.exe -- COMMAND",
            file=sys.stderr,
        )
        return 125
    application = arguments[1]
    arguments = arguments[3:]
    if not arguments:
        print("Windows Job Object runner requires a command", file=sys.stderr)
        return 125
    try:
        return _run(application, arguments)
    except OSError as error:
        print(f"Windows Job Object setup failed: {error}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
