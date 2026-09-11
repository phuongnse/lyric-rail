use std::{
    env,
    ffi::OsStr,
    fs,
    path::{Component, Path, PathBuf},
    sync::OnceLock,
};

use lrail_format::runtime::{
    RUNTIME_MANIFEST_NAME, RUNTIME_SIGNATURE_NAME, runtime_platform, verify_runtime_pack,
};
use serde::Deserialize;

const RUNTIME_PUBLIC_KEY: &str = include_str!("../../../../config/runtime-signing-public.key");
const MODEL_CACHE_POLICY_JSON: &str =
    include_str!("../../../../src/lyricrail/model_cache_policy.json");

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModelCachePathGrammar {
    separator: String,
    reject_empty_segments: bool,
    reject_dot_segments: bool,
    reject_parent_segments: bool,
    reject_backslash: bool,
    reject_colon: bool,
    reject_nul: bool,
    reject_absolute: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModelCacheCanonicalContainment {
    snapshot_directory_under_cache: bool,
    target_regular_file_under_cache: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModelCachePolicy {
    schema_version: u32,
    cache_root: String,
    path_grammar: ModelCachePathGrammar,
    canonical_containment: ModelCacheCanonicalContainment,
}

fn model_cache_policy() -> Result<&'static ModelCachePolicy, String> {
    static POLICY: OnceLock<Result<ModelCachePolicy, String>> = OnceLock::new();
    POLICY
        .get_or_init(|| {
            let policy = serde_json::from_str::<ModelCachePolicy>(MODEL_CACHE_POLICY_JSON)
                .map_err(|error| format!("Model cache policy is invalid: {error}"))?;
            let grammar = &policy.path_grammar;
            let containment = &policy.canonical_containment;
            if policy.schema_version != 1
                || policy.cache_root != "models/huggingface"
                || grammar.separator != "/"
                || !grammar.reject_empty_segments
                || !grammar.reject_dot_segments
                || !grammar.reject_parent_segments
                || !grammar.reject_backslash
                || !grammar.reject_colon
                || !grammar.reject_nul
                || !grammar.reject_absolute
                || !containment.snapshot_directory_under_cache
                || !containment.target_regular_file_under_cache
            {
                return Err(
                    "Model cache policy does not match the supported containment grammar".into(),
                );
            }
            Ok(policy)
        })
        .as_ref()
        .map_err(|error| error.clone())
}

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

fn contained_snapshot_file(
    policy: &ModelCachePolicy,
    cache_root: &Path,
    snapshot: &Path,
    path: &Path,
) -> bool {
    if !policy.canonical_containment.snapshot_directory_under_cache
        || !policy.canonical_containment.target_regular_file_under_cache
    {
        return false;
    }
    if !path.starts_with(snapshot) {
        return false;
    }
    let Ok(cache_root) = cache_root.canonicalize() else {
        return false;
    };
    let Ok(snapshot) = snapshot.canonicalize() else {
        return false;
    };
    if !snapshot.starts_with(&cache_root) || !snapshot.is_dir() {
        return false;
    }
    path.canonicalize()
        .is_ok_and(|path| path.starts_with(&cache_root) && path.is_file())
}

fn snapshot_entry(policy: &ModelCachePolicy, snapshot: &Path, filename: &str) -> Option<PathBuf> {
    let grammar = &policy.path_grammar;
    if (grammar.reject_empty_segments && filename.is_empty())
        || (grammar.reject_backslash && filename.contains('\\'))
        || (grammar.reject_colon && filename.contains(':'))
        || (grammar.reject_nul && filename.contains('\0'))
        || (grammar.reject_empty_segments && filename.split('/').any(str::is_empty))
        || (grammar.reject_dot_segments && filename.split('/').any(|part| part == "."))
        || (grammar.reject_parent_segments && filename.split('/').any(|part| part == ".."))
    {
        return None;
    }
    let relative = Path::new(filename);
    if (grammar.reject_absolute && relative.is_absolute())
        || relative.components().any(|component| {
            matches!(
                component,
                Component::ParentDir
                    | Component::RootDir
                    | Component::Prefix(_)
                    | Component::CurDir
            )
        })
    {
        return None;
    }
    Some(snapshot.join(relative))
}

fn tool_filename(name: &str) -> PathBuf {
    PathBuf::from(if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_owned()
    })
}

fn resolve_tool_path(
    environment_value: Option<&OsStr>,
    path_value: Option<&OsStr>,
    name: &str,
) -> Option<PathBuf> {
    environment_value
        .map(PathBuf::from)
        .filter(|path| path.is_file())
        .or_else(|| {
            path_value.and_then(|value| {
                env::split_paths(value).find_map(|directory| {
                    let candidate = directory.join(tool_filename(name));
                    candidate.is_file().then_some(candidate)
                })
            })
        })
}

pub(crate) fn development_tool_path(name: &str, environment_variable: &str) -> Option<PathBuf> {
    if !cfg!(debug_assertions) {
        return None;
    }
    resolve_tool_path(
        env::var_os(environment_variable).as_deref(),
        env::var_os("PATH").as_deref(),
        name,
    )
}

pub(crate) fn model_files_present_at(root: &Path) -> Result<(), String> {
    let policy = model_cache_policy()?;
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
                let huggingface_root = root.join(&policy.cache_root);
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
                let snapshot = huggingface_root
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
                            !snapshot_entry(policy, &snapshot, filename).is_some_and(|path| {
                                contained_snapshot_file(policy, &huggingface_root, &snapshot, &path)
                            })
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
    use super::{model_cache_policy, model_files_present_at, resolve_tool_path, snapshot_entry};
    use std::{env, fs, path::Path};

    fn link_file(target: &Path, link: &Path) -> bool {
        #[cfg(windows)]
        {
            std::os::windows::fs::symlink_file(target, link).is_ok()
        }
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target, link).is_ok()
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = (target, link);
            false
        }
    }

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

    #[test]
    fn model_cache_grammar_uses_the_shared_policy_fixtures() {
        let policy_json: serde_json::Value =
            serde_json::from_str(super::MODEL_CACHE_POLICY_JSON).unwrap();
        let policy = model_cache_policy().unwrap();
        let snapshot = Path::new("snapshot");
        for case in policy_json["lexicalCases"].as_array().unwrap() {
            let filename = case["filename"].as_str().unwrap();
            assert_eq!(
                snapshot_entry(policy, snapshot, filename).is_some(),
                case["accepted"].as_bool().unwrap(),
                "{}",
                case["name"].as_str().unwrap(),
            );
        }
    }

    #[test]
    fn model_presence_hint_accepts_huggingface_snapshot_links_into_cache() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("config")).unwrap();
        fs::write(
            root.path().join("config/model-manifest.json"),
            r#"{"models":{"aligner":{"type":"huggingface-snapshot","repository":"owner/model","revision":"abc","requiredFiles":["config.json","pytorch_model.bin"]}}}"#,
        )
        .unwrap();
        let cache_root = root.path().join("models/huggingface");
        let blobs = cache_root.join("models--owner--model/blobs");
        let snapshot = cache_root.join("models--owner--model/snapshots/abc");
        fs::create_dir_all(&blobs).unwrap();
        fs::create_dir_all(&snapshot).unwrap();
        let config_blob = blobs.join("config-blob");
        let weights_blob = blobs.join("weights-blob");
        fs::write(&config_blob, b"{}").unwrap();
        fs::write(&weights_blob, b"weights").unwrap();
        assert!(
            link_file(&config_blob, &snapshot.join("config.json")),
            "the supported test platform must create a file symlink"
        );
        assert!(
            link_file(&weights_blob, &snapshot.join("pytorch_model.bin")),
            "the supported test platform must create a file symlink"
        );
        assert!(model_files_present_at(root.path()).is_ok());
    }

    #[test]
    fn model_presence_hint_rejects_snapshot_links_outside_cache() {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("config")).unwrap();
        fs::write(
            root.path().join("config/model-manifest.json"),
            r#"{"models":{"aligner":{"type":"huggingface-snapshot","repository":"owner/model","revision":"abc","requiredFiles":["config.json"]}}}"#,
        )
        .unwrap();
        let cache_root = root.path().join("models/huggingface");
        let snapshot = cache_root.join("models--owner--model/snapshots/abc");
        let outside = root.path().join("outside.json");
        fs::create_dir_all(&snapshot).unwrap();
        fs::write(&outside, b"{}").unwrap();
        assert!(
            link_file(&outside, &snapshot.join("config.json")),
            "the supported test platform must create a file symlink"
        );
        assert!(model_files_present_at(root.path()).is_err());
        fs::remove_file(snapshot.join("config.json")).unwrap();
        let missing_target = cache_root.join("models--owner--model/blobs/missing-blob");
        assert!(
            link_file(&missing_target, &snapshot.join("config.json")),
            "the supported test platform must create a dangling file symlink"
        );
        assert!(model_files_present_at(root.path()).is_err());
    }

    #[test]
    fn development_tool_lookup_prefers_environment_then_path() {
        let root = tempfile::tempdir().unwrap();
        let explicit = root
            .path()
            .join("explicit")
            .join(super::tool_filename("ffmpeg"));
        let path_candidate = root
            .path()
            .join("path")
            .join(super::tool_filename("ffmpeg"));
        fs::create_dir_all(explicit.parent().unwrap()).unwrap();
        fs::create_dir_all(path_candidate.parent().unwrap()).unwrap();
        fs::write(&explicit, b"explicit").unwrap();
        fs::write(&path_candidate, b"path").unwrap();
        let joined = env::join_paths([path_candidate.parent().unwrap()]).unwrap();
        assert_eq!(
            resolve_tool_path(
                Some(explicit.as_os_str()),
                Some(joined.as_os_str()),
                "ffmpeg",
            ),
            Some(explicit.clone()),
        );
        assert_eq!(
            resolve_tool_path(None, Some(joined.as_os_str()), "ffmpeg"),
            Some(path_candidate),
        );
        assert_eq!(resolve_tool_path(None, None, "ffmpeg"), None);
    }
}
