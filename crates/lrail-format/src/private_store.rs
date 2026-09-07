use hkdf::Hkdf;
use sha2::Sha256;
use zeroize::Zeroizing;

use crate::{
    Error, LockedSecret, Result,
    crypto::{KEY_BYTES, NONCE_BYTES, TAG_BYTES, decrypt, encrypt, random_array},
};

const MAGIC: &[u8; 12] = b"LRAILSTORE\0\0";
const VERSION: u16 = 1;
const HEADER_BYTES: usize = MAGIC.len() + 2 + NONCE_BYTES;
const MAX_DOMAIN_BYTES: usize = 128;
const MAX_PLAINTEXT_BYTES: usize = 64 * 1024 * 1024;

fn derive_store_key(master: &[u8; KEY_BYTES], domain: &str) -> Result<LockedSecret<KEY_BYTES>> {
    if domain.is_empty() || domain.len() > MAX_DOMAIN_BYTES || domain.contains('\0') {
        return Err(Error::InvalidFormat("invalid private-store domain".into()));
    }
    let hkdf = Hkdf::<Sha256>::new(Some(b"LyricRail/private-store/v1"), master);
    let mut key = LockedSecret::<KEY_BYTES>::zeroed()?;
    hkdf.expand(domain.as_bytes(), key.as_mut())
        .map_err(|_| Error::PasswordDerivation)?;
    Ok(key)
}

fn aad(domain: &str) -> Vec<u8> {
    let mut output = Vec::with_capacity(MAGIC.len() + 2 + domain.len());
    output.extend_from_slice(MAGIC);
    output.extend_from_slice(&VERSION.to_le_bytes());
    output.extend_from_slice(domain.as_bytes());
    output
}

/// Authenticated encryption for small application-owned records such as the
/// private library catalog. This is deliberately separate from the `.lrail`
/// package format and never stores a key beside the ciphertext.
pub fn seal_library_record(
    master: &[u8; KEY_BYTES],
    domain: &str,
    plaintext: &[u8],
) -> Result<Vec<u8>> {
    if plaintext.len() > MAX_PLAINTEXT_BYTES {
        return Err(Error::InvalidFormat(
            "private-store record exceeds the size limit".into(),
        ));
    }
    let key = derive_store_key(master, domain)?;
    let nonce = random_array::<NONCE_BYTES>();
    let ciphertext = encrypt(&key, &nonce, &aad(domain), plaintext)?;
    let mut output = Vec::with_capacity(HEADER_BYTES + ciphertext.len());
    output.extend_from_slice(MAGIC);
    output.extend_from_slice(&VERSION.to_le_bytes());
    output.extend_from_slice(&nonce);
    output.extend_from_slice(&ciphertext);
    Ok(output)
}

pub fn open_library_record(
    master: &[u8; KEY_BYTES],
    domain: &str,
    encoded: &[u8],
) -> Result<Zeroizing<Vec<u8>>> {
    if encoded.len() < HEADER_BYTES + TAG_BYTES
        || encoded.len() > HEADER_BYTES + MAX_PLAINTEXT_BYTES + TAG_BYTES
        || &encoded[..MAGIC.len()] != MAGIC
    {
        return Err(Error::InvalidFormat(
            "private-store record has invalid bounds or magic".into(),
        ));
    }
    let version = u16::from_le_bytes(
        encoded[MAGIC.len()..MAGIC.len() + 2]
            .try_into()
            .expect("fixed version field"),
    );
    if version != VERSION {
        return Err(Error::Unsupported(format!(
            "private-store version {version}"
        )));
    }
    let nonce_start = MAGIC.len() + 2;
    let nonce: [u8; NONCE_BYTES] = encoded[nonce_start..HEADER_BYTES]
        .try_into()
        .expect("fixed nonce field");
    let key = derive_store_key(master, domain)?;
    decrypt(&key, &nonce, &aad(domain), &encoded[HEADER_BYTES..])
}

#[cfg(test)]
mod tests {
    use super::{open_library_record, seal_library_record};

    #[test]
    fn private_records_are_domain_bound_and_authenticated() {
        let master = [0x61; 32];
        let encoded = seal_library_record(&master, "catalog", b"private lyrics").unwrap();
        assert_eq!(
            open_library_record(&master, "catalog", &encoded)
                .unwrap()
                .as_slice(),
            b"private lyrics"
        );
        assert!(open_library_record(&master, "oauth", &encoded).is_err());
        assert!(open_library_record(&[0x62; 32], "catalog", &encoded).is_err());

        let mut corrupted = encoded;
        *corrupted.last_mut().unwrap() ^= 1;
        assert!(open_library_record(&master, "catalog", &corrupted).is_err());
    }
}
