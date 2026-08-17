use crate::{Error, Result};

pub const MAGIC: [u8; 8] = *b"LRAIL\r\n\x1a";
pub const HEADER_SIZE: usize = 128;
pub const FORMAT_MAJOR: u16 = 1;
pub const FORMAT_MINOR: u16 = 0;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Header {
    pub flags: u32,
    pub package_id: [u8; 16],
    pub envelope_offset: u64,
    pub envelope_length: u64,
    pub manifest_offset: u64,
    pub manifest_length: u64,
    pub manifest_nonce: [u8; 24],
    pub package_length: u64,
}

impl Header {
    pub fn encode(&self) -> [u8; HEADER_SIZE] {
        let mut bytes = [0_u8; HEADER_SIZE];
        bytes[0..8].copy_from_slice(&MAGIC);
        bytes[8..10].copy_from_slice(&FORMAT_MAJOR.to_le_bytes());
        bytes[10..12].copy_from_slice(&FORMAT_MINOR.to_le_bytes());
        bytes[12..16].copy_from_slice(&self.flags.to_le_bytes());
        bytes[16..32].copy_from_slice(&self.package_id);
        bytes[32..40].copy_from_slice(&self.envelope_offset.to_le_bytes());
        bytes[40..48].copy_from_slice(&self.envelope_length.to_le_bytes());
        bytes[48..56].copy_from_slice(&self.manifest_offset.to_le_bytes());
        bytes[56..64].copy_from_slice(&self.manifest_length.to_le_bytes());
        bytes[64..88].copy_from_slice(&self.manifest_nonce);
        bytes[88..96].copy_from_slice(&self.package_length.to_le_bytes());
        bytes
    }

    pub fn decode(bytes: &[u8; HEADER_SIZE]) -> Result<Self> {
        if bytes[0..8] != MAGIC {
            return Err(Error::InvalidFormat("magic bytes do not match".into()));
        }
        let major = u16::from_le_bytes(bytes[8..10].try_into().unwrap());
        let minor = u16::from_le_bytes(bytes[10..12].try_into().unwrap());
        if major != FORMAT_MAJOR {
            return Err(Error::Unsupported(format!(
                "format major {major}; this reader supports {FORMAT_MAJOR}"
            )));
        }
        if minor > FORMAT_MINOR {
            return Err(Error::Unsupported(format!(
                "format minor {minor}; this reader supports through {FORMAT_MINOR}"
            )));
        }
        if bytes[96..].iter().any(|value| *value != 0) {
            return Err(Error::Unsupported(
                "reserved header bytes are non-zero".into(),
            ));
        }
        let flags = u32::from_le_bytes(bytes[12..16].try_into().unwrap());
        if flags != 0 {
            return Err(Error::Unsupported(format!(
                "unknown mandatory flags 0x{flags:x}"
            )));
        }
        Ok(Self {
            flags,
            package_id: bytes[16..32].try_into().unwrap(),
            envelope_offset: u64::from_le_bytes(bytes[32..40].try_into().unwrap()),
            envelope_length: u64::from_le_bytes(bytes[40..48].try_into().unwrap()),
            manifest_offset: u64::from_le_bytes(bytes[48..56].try_into().unwrap()),
            manifest_length: u64::from_le_bytes(bytes[56..64].try_into().unwrap()),
            manifest_nonce: bytes[64..88].try_into().unwrap(),
            package_length: u64::from_le_bytes(bytes[88..96].try_into().unwrap()),
        })
    }
}
