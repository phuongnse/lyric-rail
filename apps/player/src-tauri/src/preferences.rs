use std::{fs, io::Write, path::PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};
use tempfile::NamedTempFile;

use crate::runtime;

const MAX_PREFERENCES_BYTES: u64 = 64 * 1024;

#[derive(Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PreferencesDocument {
    library_path: Option<PathBuf>,
    cache_path: Option<PathBuf>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreferencesSnapshot {
    pub library_path: String,
    pub cache_path: String,
    pub default_library_path: String,
    pub default_cache_path: String,
    pub runtime_integrity: String,
    pub install_allowed: bool,
    pub model_catalog: Vec<runtime::ModelCatalogEntry>,
}

fn preferences_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_config_dir()
        .map_err(|error| format!("Unable to resolve Preferences directory: {error}"))?
        .join("preferences.json"))
}

fn default_paths(app: &AppHandle) -> Result<(PathBuf, PathBuf), String> {
    let data = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Unable to resolve Library directory: {error}"))?;
    let cache = app
        .path()
        .app_cache_dir()
        .map_err(|error| format!("Unable to resolve cache directory: {error}"))?;
    Ok((data, cache))
}

fn existing_directory(path: PathBuf, label: &str) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err(format!("{label} must be an absolute directory"));
    }
    let metadata =
        fs::symlink_metadata(&path).map_err(|_| format!("{label} is no longer available"))?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(format!("{label} must be a regular directory"));
    }
    path.canonicalize()
        .map_err(|_| format!("{label} could not be resolved"))
}

fn read_document(app: &AppHandle) -> Result<PreferencesDocument, String> {
    let path = preferences_path(app)?;
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(PreferencesDocument::default());
        }
        Err(_) => return Err("Preferences could not be inspected".into()),
    };
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() > MAX_PREFERENCES_BYTES
    {
        return Err("Preferences file is invalid or exceeds its size limit".into());
    }
    let encoded = fs::read(&path).map_err(|_| "Preferences could not be read".to_string())?;
    serde_json::from_slice(&encoded).map_err(|_| "Preferences file is invalid".into())
}

fn effective_path(
    configured: Option<PathBuf>,
    default: PathBuf,
    label: &str,
) -> Result<PathBuf, String> {
    configured.map_or(Ok(default.clone()), |path| existing_directory(path, label))
}

pub fn snapshot(app: &AppHandle) -> Result<PreferencesSnapshot, String> {
    let (default_library, default_cache) = default_paths(app)?;
    let document = read_document(app)?;
    let library = effective_path(
        document.library_path,
        default_library.clone(),
        "Library location",
    )?;
    let cache = effective_path(document.cache_path, default_cache.clone(), "Cache location")?;
    let models = runtime::model_catalog()?;
    Ok(PreferencesSnapshot {
        library_path: library.display().to_string(),
        cache_path: cache.display().to_string(),
        default_library_path: default_library.display().to_string(),
        default_cache_path: default_cache.display().to_string(),
        runtime_integrity: models.runtime_integrity,
        install_allowed: models.install_allowed,
        model_catalog: models.models,
    })
}

pub fn cache_directory(app: &AppHandle) -> Result<PathBuf, String> {
    let (_, default_cache) = default_paths(app)?;
    let document = read_document(app)?;
    effective_path(document.cache_path, default_cache, "Cache location")
}

pub fn save(
    app: &AppHandle,
    library_path: Option<String>,
    cache_path: Option<String>,
) -> Result<PreferencesSnapshot, String> {
    let library_path = library_path
        .filter(|path| !path.trim().is_empty())
        .map(PathBuf::from)
        .map(|path| existing_directory(path, "Library location"))
        .transpose()?;
    let cache_path = cache_path
        .filter(|path| !path.trim().is_empty())
        .map(PathBuf::from)
        .map(|path| existing_directory(path, "Cache location"))
        .transpose()?;
    let encoded = serde_json::to_vec(&PreferencesDocument {
        library_path,
        cache_path,
    })
    .map_err(|_| "Preferences could not be encoded".to_string())?;
    if encoded.len() as u64 > MAX_PREFERENCES_BYTES {
        return Err("Preferences exceed their size limit".into());
    }
    let path = preferences_path(app)?;
    let parent = path
        .parent()
        .ok_or_else(|| "Preferences directory is unavailable".to_string())?;
    fs::create_dir_all(parent)
        .map_err(|_| "Preferences directory could not be created".to_string())?;
    let mut temporary = NamedTempFile::new_in(parent)
        .map_err(|_| "Preferences temporary file could not be created".to_string())?;
    temporary
        .write_all(&encoded)
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|_| "Preferences could not be written".to_string())?;
    if path.exists() {
        fs::remove_file(&path)
            .map_err(|_| "Existing Preferences could not be replaced".to_string())?;
    }
    temporary
        .persist(&path)
        .map_err(|_| "Preferences could not be published".to_string())?;
    snapshot(app)
}
