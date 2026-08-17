#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

#[cfg(windows)]
mod windows_service;

#[cfg(windows)]
fn main() -> ::windows_service::Result<()> {
    windows_service::run()
}

#[cfg(not(windows))]
fn main() {
    eprintln!("LyricRail Volume Broker is available only on Windows.");
    std::process::exit(1);
}
