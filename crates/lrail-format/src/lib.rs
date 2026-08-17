//! Authenticated, random-access LyricRail package format.
//!
//! The package reader treats all offsets and lengths as attacker-controlled.
//! Plaintext is returned only after XChaCha20-Poly1305 authentication succeeds.

mod crypto;
mod error;
mod header;
mod package;
mod recovery;
mod rotation;
pub mod runtime;
mod schema;
mod secret;
mod vault;

pub use error::{Error, Result};
pub use header::{FORMAT_MAJOR, FORMAT_MINOR, HEADER_SIZE, Header, MAGIC};
pub use package::{
    PackageReader, PackagedAsset, RewrappedPackage, inspect_package, pack, pack_for_vault,
    rewrap_package_for_vaults, verify_package, verify_package_with_vault,
    verify_package_with_vault_candidates,
};
pub use recovery::{
    RecoveryBundleExport, RecoveryBundleInspection, RecoveryBundleVerification,
    RecoveryRestoreReport, export_recovery_bundle, inspect_recovery_bundle,
    restore_recovery_bundle, verify_recovery_bundle,
};
pub use rotation::{
    RotationReport, RotationStatus, library_master_rotation_status, rotate_library_master,
};
pub use schema::{
    AssetRequest, ContentEncoding, KeyEnvelope, Manifest, PackageInspection, PackageRequest,
    VerificationReport,
};
pub use secret::{LockedSecret, LockedString};
pub use vault::{load_or_create_vault_master, load_vault_master, pack_for_device_vault};
