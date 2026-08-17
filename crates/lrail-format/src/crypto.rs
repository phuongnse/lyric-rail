use argon2::{Algorithm, Argon2, Params, Version};
use chacha20poly1305::{
    KeyInit, XChaCha20Poly1305, XNonce,
    aead::{Aead, Payload},
};
use hkdf::Hkdf;
use rand::{RngCore, rngs::OsRng};
use sha2::Sha256;
use zeroize::Zeroizing;

use crate::{Error, LockedSecret, Result};

pub const KEY_BYTES: usize = 32;
pub const NONCE_BYTES: usize = 24;
pub const TAG_BYTES: usize = 16;
pub const RECOVERY_SALT_BYTES: usize = 16;
pub const ARGON_MEMORY_KIB: u32 = 64 * 1024;
pub const ARGON_ITERATIONS: u32 = 3;
pub const ARGON_LANES: u32 = 1;
pub const ARGON_MAX_MEMORY_KIB: u32 = 1024 * 1024;
pub const ARGON_MAX_ITERATIONS: u32 = 10;
pub const ARGON_MAX_LANES: u32 = 16;

pub fn random_array<const N: usize>() -> [u8; N] {
    let mut bytes = [0_u8; N];
    OsRng.fill_bytes(&mut bytes);
    bytes
}

pub fn derive_recovery_kek(
    passphrase: &[u8],
    salt: &[u8],
    memory_kib: u32,
    iterations: u32,
    lanes: u32,
) -> Result<LockedSecret<KEY_BYTES>> {
    if salt.len() < RECOVERY_SALT_BYTES
        || memory_kib < ARGON_MEMORY_KIB
        || iterations < ARGON_ITERATIONS
        || memory_kib > ARGON_MAX_MEMORY_KIB
        || iterations > ARGON_MAX_ITERATIONS
        || lanes == 0
        || lanes > ARGON_MAX_LANES
    {
        return Err(Error::InvalidFormat(
            "recovery KDF parameters are below the v1 security floor".into(),
        ));
    }
    let params = Params::new(memory_kib, iterations, lanes, Some(KEY_BYTES))
        .map_err(|_| Error::PasswordDerivation)?;
    let argon = Argon2::new(Algorithm::Argon2id, Version::V0x13, params);
    let mut output = LockedSecret::<KEY_BYTES>::zeroed()?;
    argon
        .hash_password_into(passphrase, salt, output.as_mut())
        .map_err(|_| Error::PasswordDerivation)?;
    Ok(output)
}

pub fn derive_vault_kek(
    vault_master_key: &[u8; KEY_BYTES],
    package_id: &[u8; 16],
    key_id: &[u8; 16],
) -> Result<LockedSecret<KEY_BYTES>> {
    let hkdf = Hkdf::<Sha256>::new(Some(package_id), vault_master_key);
    let mut info = b"LyricRail/v1/os-vault-kek".to_vec();
    info.extend_from_slice(key_id);
    let mut output = LockedSecret::<KEY_BYTES>::zeroed()?;
    hkdf.expand(&info, output.as_mut())
        .map_err(|_| Error::PasswordDerivation)?;
    Ok(output)
}

pub fn encrypt(
    key: &[u8; KEY_BYTES],
    nonce: &[u8; NONCE_BYTES],
    aad: &[u8],
    data: &[u8],
) -> Result<Vec<u8>> {
    let cipher = XChaCha20Poly1305::new_from_slice(key).map_err(|_| Error::Authentication)?;
    cipher
        .encrypt(XNonce::from_slice(nonce), Payload { msg: data, aad })
        .map_err(|_| Error::Authentication)
}

pub fn decrypt(
    key: &[u8; KEY_BYTES],
    nonce: &[u8; NONCE_BYTES],
    aad: &[u8],
    data: &[u8],
) -> Result<Zeroizing<Vec<u8>>> {
    let cipher = XChaCha20Poly1305::new_from_slice(key).map_err(|_| Error::Authentication)?;
    cipher
        .decrypt(XNonce::from_slice(nonce), Payload { msg: data, aad })
        .map(Zeroizing::new)
        .map_err(|_| Error::Authentication)
}

#[cfg(test)]
mod tests {
    use serde::Deserialize;

    use super::{KEY_BYTES, NONCE_BYTES, decrypt, encrypt};

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct XChaChaFixture {
        schema_version: u32,
        algorithm: String,
        key_hex: String,
        nonce_hex: String,
        aad_hex: String,
        plaintext_hex: String,
        ciphertext_hex: String,
    }

    fn decoded<const N: usize>(value: &str) -> [u8; N] {
        hex::decode(value)
            .unwrap()
            .try_into()
            .unwrap_or_else(|value: Vec<u8>| panic!("expected {N} bytes, got {}", value.len()))
    }

    #[test]
    fn matches_versioned_libsodium_xchacha_fixture() {
        let fixture: XChaChaFixture = serde_json::from_str(include_str!(
            "../../../tests/fixtures/xchacha20poly1305.json"
        ))
        .unwrap();
        assert_eq!(fixture.schema_version, 1);
        assert_eq!(fixture.algorithm, "XChaCha20-Poly1305-IETF");

        let key = decoded::<KEY_BYTES>(&fixture.key_hex);
        let nonce = decoded::<NONCE_BYTES>(&fixture.nonce_hex);
        let aad = hex::decode(&fixture.aad_hex).unwrap();
        let plaintext = hex::decode(&fixture.plaintext_hex).unwrap();
        let ciphertext = hex::decode(&fixture.ciphertext_hex).unwrap();

        assert_eq!(encrypt(&key, &nonce, &aad, &plaintext).unwrap(), ciphertext);
        assert_eq!(
            decrypt(&key, &nonce, &aad, &ciphertext).unwrap().as_slice(),
            plaintext
        );

        let mut corrupted = ciphertext;
        *corrupted.last_mut().unwrap() ^= 1;
        assert!(decrypt(&key, &nonce, &aad, &corrupted).is_err());
    }
}
