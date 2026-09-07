const DESKTOP_ICON_INPUTS: &[&str] = &[
    "icons/32x32.png",
    "icons/128x128.png",
    "icons/128x128@2x.png",
    "icons/icon.icns",
    "icons/icon.ico",
];

fn main() {
    for icon in DESKTOP_ICON_INPUTS {
        println!("cargo:rerun-if-changed={icon}");
    }
    tauri_build::build()
}
