use thiserror::Error;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Error)]
pub enum Error {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid LyricRail package: {0}")]
    InvalidFormat(String),
    #[error("unsupported LyricRail package: {0}")]
    Unsupported(String),
    #[error("package authentication failed")]
    Authentication,
    #[error("wrong recovery passphrase or corrupted key envelope")]
    KeyUnwrap,
    #[error("CBOR encoding failed: {0}")]
    CborEncode(String),
    #[error("CBOR decoding failed: {0}")]
    CborDecode(String),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("password derivation failed")]
    PasswordDerivation,
    #[error("OS credential vault error: {0}")]
    Vault(String),
    #[error("the operating system refused to lock secret memory")]
    MemoryLock,
    #[error("asset source is invalid: {0}")]
    InvalidAsset(String),
    #[error("library master rotation failed: {0}")]
    Rotation(String),
    #[error("library recovery failed: {0}")]
    Recovery(String),
}
