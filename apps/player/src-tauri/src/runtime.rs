use std::{
    env, fs,
    path::{Path, PathBuf},
    sync::OnceLock,
};

use lrail_format::runtime::{
    RUNTIME_MANIFEST_NAME, RUNTIME_SIGNATURE_NAME, runtime_platform, verify_runtime_pack,
};

const RUNTIME_PUBLIC_KEY: &str = include_str!("../../../../config/runtime-signing-public.key");

#[derive(Debug, Clone)]
pub struct ResolvedRuntime {
    pub root: PathBuf,
    pub python: PathBuf,
    pub ffmpeg: Option<PathBuf>,
    pub ffprobe: Option<PathBuf>,
    pub lrail: Option<PathBuf>,
    pub integrity: &'static str,
}

fn canonical_runtime_root(root: &Path) -> Result<PathBuf, String> {
    let root = root
        .canonicalize()
        .map_err(|error| format!("Unable to open runtime root {}: {error}", root.display()))?;
    if !root.join("config/pipeline.json").is_file() {
        return Err(format!(
            "Runtime root {} does not contain config/pipeline.json",
            root.display()
        ));
    }
    Ok(root)
}

fn project_root() -> Result<PathBuf, String> {
    if let Some(configured) = env::var_os("LYRICRAIL_HOME") {
        return canonical_runtime_root(&PathBuf::from(configured));
    }
    let mut candidates = Vec::new();
    if let Ok(executable) = env::current_exe()
        && let Some(directory) = executable.parent()
    {
        candidates.push(directory.join("runtime"));
        candidates.push(directory.to_owned());
    }
    if cfg!(debug_assertions) {
        let current = env::current_dir().map_err(|error| error.to_string())?;
        candidates.extend(current.ancestors().map(Path::to_owned));
    }
    candidates
        .into_iter()
        .find(|root| root.join("config/pipeline.json").is_file())
        .map(|root| canonical_runtime_root(&root))
        .transpose()?
        .ok_or_else(|| "Unable to locate the LyricRail core runtime. Set LYRICRAIL_HOME.".into())
}

fn development_python(root: &Path) -> Result<PathBuf, String> {
    let candidates = [
        env::var_os("LYRICRAIL_PYTHON").map(PathBuf::from),
        Some(root.join("runtime/python").join(if cfg!(windows) {
            "python.exe"
        } else {
            "python"
        })),
        Some(root.join(".venv").join(if cfg!(windows) {
            "Scripts/python.exe"
        } else {
            "bin/python"
        })),
    ];
    candidates
        .into_iter()
        .flatten()
        .find(|path| path.is_file())
        .ok_or_else(|| "The pinned Python runtime is unavailable. Set LYRICRAIL_PYTHON.".into())
}

pub fn resolve_runtime() -> Result<ResolvedRuntime, String> {
    let root = project_root()?;
    let manifest = root.join(RUNTIME_MANIFEST_NAME);
    let signature = root.join(RUNTIME_SIGNATURE_NAME);
    if manifest.is_file() || signature.is_file() {
        if !(manifest.is_file() && signature.is_file()) {
            return Err("The runtime manifest/signature pair is incomplete".into());
        }
        let verified = verify_runtime_pack(
            &root,
            RUNTIME_PUBLIC_KEY.trim(),
            env!("CARGO_PKG_VERSION"),
            &runtime_platform(),
        )
        .map_err(|error| format!("Runtime integrity verification failed: {error:#}"))?;
        return Ok(ResolvedRuntime {
            root,
            python: verified.python_executable,
            ffmpeg: Some(verified.ffmpeg_executable),
            ffprobe: Some(verified.ffprobe_executable),
            lrail: Some(verified.lrail_executable),
            integrity: "signed-verified",
        });
    }
    if !cfg!(debug_assertions) {
        return Err("Release LyricRail requires a signed runtime manifest and signature".into());
    }
    let python = development_python(&root)?;
    let lrail = [
        root.join("target/debug")
            .join(if cfg!(windows) { "lrail.exe" } else { "lrail" }),
        root.join("runtime/bin")
            .join(if cfg!(windows) { "lrail.exe" } else { "lrail" }),
    ]
    .into_iter()
    .find(|path| path.is_file());
    Ok(ResolvedRuntime {
        root,
        python,
        ffmpeg: None,
        ffprobe: None,
        lrail,
        integrity: "development-unverified",
    })
}

pub fn runtime_available_hint() -> Result<(), String> {
    static RUNTIME_HINT: OnceLock<Result<(), String>> = OnceLock::new();
    RUNTIME_HINT
        .get_or_init(|| resolve_runtime().map(|_| ()))
        .clone()
}

fn contained_regular_file(root: &Path, path: &Path) -> bool {
    let Ok(root) = root.canonicalize() else {
        return false;
    };
    path.canonicalize()
        .is_ok_and(|path| path.starts_with(&root) && path.is_file())
}

fn model_files_present_at(root: &Path) -> Result<(), String> {
    let manifest_path = root.join("config/model-manifest.json");
    let encoded =
        fs::read(&manifest_path).map_err(|_| "Pinned model manifest is unavailable".to_string())?;
    if encoded.len() > 1024 * 1024 {
        return Err("Pinned model manifest exceeds its 1 MiB bound".into());
    }
    let manifest: serde_json::Value = serde_json::from_slice(&encoded)
        .map_err(|_| "Pinned model manifest is invalid".to_string())?;
    let models = manifest
        .get("models")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| "Pinned model manifest has no model map".to_string())?;
    let mut missing = 0_usize;
    for model in models.values() {
        match model.get("type").and_then(serde_json::Value::as_str) {
            Some("audio-separator-checkpoint") => {
                let audio_root = root.join("models/audio-separator");
                if let Some(filename) = model.get("filename").and_then(serde_json::Value::as_str)
                    && !contained_regular_file(&audio_root, &audio_root.join(filename))
                {
                    missing += 1;
                }
                if let Some(associated) = model
                    .get("associatedFileSha256")
                    .and_then(serde_json::Value::as_object)
                {
                    missing += associated
                        .keys()
                        .filter(|filename| {
                            !contained_regular_file(&audio_root, &audio_root.join(filename))
                        })
                        .count();
                }
            }
            Some("huggingface-snapshot") => {
                let Some(repository) = model.get("repository").and_then(serde_json::Value::as_str)
                else {
                    missing += 1;
                    continue;
                };
                let Some(revision) = model.get("revision").and_then(serde_json::Value::as_str)
                else {
                    missing += 1;
                    continue;
                };
                let snapshot = root
                    .join("models/huggingface")
                    .join(format!("models--{}", repository.replace('/', "--")))
                    .join("snapshots")
                    .join(revision);
                if let Some(required) = model
                    .get("requiredFiles")
                    .and_then(serde_json::Value::as_array)
                {
                    missing += required
                        .iter()
                        .filter_map(serde_json::Value::as_str)
                        .filter(|filename| {
                            !contained_regular_file(&snapshot, &snapshot.join(filename))
                        })
                        .count();
                }
            }
            _ => missing += 1,
        }
    }
    if missing == 0 {
        Ok(())
    } else {
        Err(format!(
            "{missing} pinned processing model files are missing or invalid"
        ))
    }
}

pub fn model_files_present_hint() -> Result<(), String> {
    model_files_present_at(&project_root()?)
}

#[cfg(test)]
mod tests {
    use super::model_files_present_at;
    use std::fs;

    #[test]
    fn model_presence_hint_finds_audio_and_snapshot_files_without_hashing() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("config")).unwrap();
        fs::write(
            root.path().join("config/model-manifest.json"),
            r#"{"models":{"audio":{"type":"audio-separator-checkpoint","filename":"model.ckpt","associatedFileSha256":{"model.yaml":"hash"}},"aligner":{"type":"huggingface-snapshot","repository":"owner/model","revision":"abc","requiredFiles":["config.json"]}}}"#,
        )
        .unwrap();
        assert!(model_files_present_at(root.path()).is_err());
        fs::create_dir_all(root.path().join("models/audio-separator")).unwrap();
        fs::write(
            root.path().join("models/audio-separator/model.ckpt"),
            b"model",
        )
        .unwrap();
        fs::write(
            root.path().join("models/audio-separator/model.yaml"),
            b"config",
        )
        .unwrap();
        let snapshot = root
            .path()
            .join("models/huggingface/models--owner--model/snapshots/abc");
        fs::create_dir_all(&snapshot).unwrap();
        fs::write(snapshot.join("config.json"), b"{}").unwrap();
        assert!(model_files_present_at(root.path()).is_ok());
    }
}
