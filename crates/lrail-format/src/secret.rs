use std::ops::Deref;

use rand::{RngCore, rngs::OsRng};
use zeroize::{Zeroize, Zeroizing};

use crate::{Error, Result};

/// A fixed-size secret kept in non-pageable memory and zeroed before unlock.
///
/// Construction is fail-closed: callers never receive an unlocked fallback.
pub struct LockedSecret<const N: usize> {
    bytes: Box<[u8; N]>,
}

/// A UTF-8 secret whose existing heap buffer is locked and zeroized in place.
pub struct LockedString {
    value: Zeroizing<String>,
}

impl LockedString {
    pub fn new(value: String) -> Result<Self> {
        Self::new_with_lock(value, |address, length| {
            // SAFETY: the string is not mutated after locking, so its heap
            // buffer stays allocated at this address until `Drop`.
            unsafe { memsec::mlock(address, length) }
        })
    }

    fn new_with_lock(value: String, lock: impl FnOnce(*mut u8, usize) -> bool) -> Result<Self> {
        let mut value = Zeroizing::new(value);
        if value.is_empty() {
            return Err(Error::InvalidFormat(
                "secret string must not be empty".into(),
            ));
        }
        let length = value.len();
        if !lock(value.as_mut_ptr(), length) {
            value.zeroize();
            return Err(Error::MemoryLock);
        }
        Ok(Self { value })
    }
}

impl Deref for LockedString {
    type Target = str;

    fn deref(&self) -> &Self::Target {
        self.value.as_str()
    }
}

impl AsRef<str> for LockedString {
    fn as_ref(&self) -> &str {
        self.value.as_str()
    }
}

impl Drop for LockedString {
    fn drop(&mut self) {
        let address = self.value.as_mut_ptr();
        let length = self.value.len();
        self.value.zeroize();
        // SAFETY: zeroization does not reallocate the string buffer; this is
        // the exact region successfully passed to `mlock` in `new`.
        let _ = unsafe { memsec::munlock(address, length) };
    }
}

impl<const N: usize> LockedSecret<N> {
    pub fn from_slice(value: &[u8]) -> Result<Self> {
        Self::from_slice_with_lock(value, |address, length| {
            // SAFETY: `address..address + length` belongs to the boxed array and
            // remains at a stable address until `Drop` calls `munlock`.
            unsafe { memsec::mlock(address, length) }
        })
    }

    pub fn zeroed() -> Result<Self> {
        Self::from_slice(&[0_u8; N])
    }

    pub fn random() -> Result<Self> {
        let mut secret = Self::zeroed()?;
        OsRng.fill_bytes(secret.as_mut());
        Ok(secret)
    }

    fn from_slice_with_lock(
        value: &[u8],
        lock: impl FnOnce(*mut u8, usize) -> bool,
    ) -> Result<Self> {
        let value: &[u8; N] = value
            .try_into()
            .map_err(|_| Error::InvalidFormat(format!("secret must contain exactly {N} bytes")))?;
        let mut bytes = Box::new([0_u8; N]);
        let address = bytes.as_mut_ptr();
        if !lock(address, N) {
            bytes.zeroize();
            return Err(Error::MemoryLock);
        }
        bytes.copy_from_slice(value);
        Ok(Self { bytes })
    }
}

impl<const N: usize> Deref for LockedSecret<N> {
    type Target = [u8; N];

    fn deref(&self) -> &Self::Target {
        self.bytes.as_ref()
    }
}

impl<const N: usize> AsRef<[u8]> for LockedSecret<N> {
    fn as_ref(&self) -> &[u8] {
        self.bytes.as_slice()
    }
}

impl<const N: usize> AsMut<[u8; N]> for LockedSecret<N> {
    fn as_mut(&mut self) -> &mut [u8; N] {
        self.bytes.as_mut()
    }
}

impl<const N: usize> Drop for LockedSecret<N> {
    fn drop(&mut self) {
        self.bytes.zeroize();
        // SAFETY: this is the exact stable allocation successfully passed to
        // `mlock`, and it is still alive for the duration of this call.
        let _ = unsafe { memsec::munlock(self.bytes.as_mut_ptr(), N) };
    }
}

#[cfg(test)]
mod tests {
    use super::{LockedSecret, LockedString};
    use crate::Error;

    #[test]
    fn locked_secret_is_readable_and_mutable_without_an_unlocked_fallback() {
        let mut secret = LockedSecret::<32>::from_slice(&[0x41; 32]).unwrap();
        assert_eq!(secret.as_slice(), &[0x41; 32]);
        secret.as_mut()[0] = 0x42;
        assert_eq!(secret[0], 0x42);
    }

    #[test]
    fn lock_failure_is_explicit_and_fail_closed() {
        let error = LockedSecret::<32>::from_slice_with_lock(&[0x41; 32], |_, _| false)
            .err()
            .unwrap();
        assert!(matches!(error, Error::MemoryLock));
    }

    #[test]
    fn wrong_secret_length_is_rejected_before_locking() {
        assert!(LockedSecret::<32>::from_slice(&[0x41; 31]).is_err());
    }

    #[test]
    fn locked_string_uses_the_same_fail_closed_policy() {
        let secret = LockedString::new("correct horse battery staple".into()).unwrap();
        assert_eq!(secret.as_ref(), "correct horse battery staple");
        drop(secret);

        let error = LockedString::new_with_lock("sensitive".into(), |_, _| false)
            .err()
            .unwrap();
        assert!(matches!(error, Error::MemoryLock));
    }
}
