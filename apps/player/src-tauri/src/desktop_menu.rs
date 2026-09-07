use tauri::{
    AppHandle, Runtime,
    menu::{AboutMetadata, Menu, MenuBuilder, SubmenuBuilder},
};

/// macOS owns its application menu. Product actions stay in the shared in-app UI.
pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let version = app.package_info().version.to_string();
    let about = AboutMetadata {
        name: Some("LyricRail".into()),
        version: Some(version.clone()),
        short_version: Some(version),
        comments: Some("Private karaoke production and playback.".into()),
        icon: app.default_window_icon().cloned(),
        ..Default::default()
    };
    let application = SubmenuBuilder::new(app, "LyricRail")
        .about_with_text("About LyricRail", Some(about))
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        .quit()
        .build()?;
    MenuBuilder::new(app).item(&application).build()
}
