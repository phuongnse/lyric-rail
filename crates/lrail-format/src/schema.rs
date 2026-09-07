use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PackageRequest {
    pub metadata: Value,
    pub assets: Vec<AssetRequest>,
    #[serde(default = "default_producer")]
    pub producer: String,
    #[serde(default = "default_minimum_player")]
    pub minimum_player_version: String,
}

fn default_producer() -> String {
    format!("LyricRail/{}", env!("CARGO_PKG_VERSION"))
}

fn default_minimum_player() -> String {
    "0.8.0".to_owned()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AssetRequest {
    pub logical_name: String,
    pub path: PathBuf,
    pub media_type: String,
    pub kind: String,
    #[serde(default)]
    pub track_name: Option<String>,
    #[serde(default)]
    pub language: Option<String>,
    #[serde(default)]
    pub default: bool,
    #[serde(default)]
    pub content_encoding: ContentEncoding,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PackageRevisionRequest {
    #[serde(default)]
    pub metadata: Option<Value>,
    pub assets: Vec<AssetRequest>,
    #[serde(default)]
    pub producer: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ContentEncoding {
    #[default]
    Identity,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct KeyEnvelope {
    pub schema_version: u16,
    pub slots: Vec<KeySlot>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct KeySlot {
    pub mechanism: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub key_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub salt: Option<Vec<u8>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub memory_kib: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub iterations: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lanes: Option<u32>,
    pub nonce: Vec<u8>,
    pub wrapped_dek: Vec<u8>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u16,
    pub minimum_player_version: String,
    pub package_id: Uuid,
    pub created_at_unix_ms: u64,
    pub producer: String,
    pub metadata: Value,
    pub assets: Vec<Asset>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Asset {
    pub asset_id: Uuid,
    pub logical_name: String,
    pub media_type: String,
    pub kind: String,
    pub track_name: Option<String>,
    pub language: Option<String>,
    pub default: bool,
    pub content_encoding: ContentEncoding,
    pub plaintext_length: u64,
    pub sha256: Vec<u8>,
    pub chunks: Vec<Chunk>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Chunk {
    pub index: u64,
    pub plaintext_offset: u64,
    pub plaintext_length: u32,
    pub file_offset: u64,
    pub ciphertext_length: u32,
    pub nonce: Vec<u8>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PackageInspection {
    pub format_major: u16,
    pub format_minor: u16,
    pub package_id: Uuid,
    pub package_length: u64,
    pub key_mechanisms: Vec<String>,
    pub encrypted_manifest_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct VerificationReport {
    pub valid: bool,
    pub package_id: Uuid,
    pub asset_count: usize,
    pub chunk_count: usize,
    pub plaintext_bytes: u64,
    pub package_bytes: u64,
    pub cryptographic_overhead_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PackageRevisionReport {
    pub package_id: Uuid,
    pub replaced_assets: Vec<String>,
    pub preserved_assets: usize,
    pub preserved_ciphertext_bytes: u64,
    pub package_bytes: u64,
}
