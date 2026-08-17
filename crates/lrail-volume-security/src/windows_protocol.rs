use crate::{VolumeProtectionState, VolumeSecurityError};

pub const BROKER_SERVICE_NAME: &str = "LyricRailVolumeBroker";
pub const BROKER_PIPE_NAME: &str = r"\\.\pipe\LyricRailVolumeBrokerV1";
pub const BROKER_PROTOCOL_VERSION: u16 = 1;
pub const BROKER_REQUEST_LEN: usize = 32;
pub const BROKER_RESPONSE_LEN: usize = 40;

const REQUEST_MAGIC: &[u8; 8] = b"LRVB1REQ";
const RESPONSE_MAGIC: &[u8; 8] = b"LRVB1RSP";
const REQUEST_OPCODE: u16 = 1;
const RESPONSE_OPCODE: u16 = 2;
const RESULT_OK: u32 = 0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BitLockerStatus {
    pub protection_status: u32,
    pub conversion_status: u32,
}

impl BitLockerStatus {
    pub fn state(self) -> VolumeProtectionState {
        classify_bitlocker(self.protection_status, self.conversion_status)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BrokerRequest {
    pub drive_letter: u8,
    pub nonce: [u8; 16],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BrokerResponse {
    pub result: u32,
    pub nonce: [u8; 16],
    pub status: BitLockerStatus,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BrokerEvidence {
    pub service_process_id: u32,
    pub status: BitLockerStatus,
}

pub fn classify_bitlocker(protection_status: u32, conversion_status: u32) -> VolumeProtectionState {
    match (protection_status, conversion_status) {
        (1, 1) => VolumeProtectionState::Protected,
        (0, 0..=5) | (1, 0 | 2..=5) => VolumeProtectionState::Unprotected,
        (2, 0..=5) => VolumeProtectionState::Unknown,
        _ => VolumeProtectionState::Unknown,
    }
}

pub fn encode_request(request: &BrokerRequest) -> Result<[u8; BROKER_REQUEST_LEN], String> {
    let drive = request.drive_letter.to_ascii_uppercase();
    if !drive.is_ascii_uppercase() {
        return Err("drive letter must be ASCII A-Z".to_owned());
    }
    let mut output = [0u8; BROKER_REQUEST_LEN];
    output[0..8].copy_from_slice(REQUEST_MAGIC);
    output[8..10].copy_from_slice(&BROKER_PROTOCOL_VERSION.to_le_bytes());
    output[10..12].copy_from_slice(&REQUEST_OPCODE.to_le_bytes());
    output[12] = drive;
    output[16..32].copy_from_slice(&request.nonce);
    Ok(output)
}

pub fn decode_request(input: &[u8]) -> Result<BrokerRequest, String> {
    if input.len() != BROKER_REQUEST_LEN {
        return Err(format!("request length must be {BROKER_REQUEST_LEN} bytes"));
    }
    if &input[0..8] != REQUEST_MAGIC {
        return Err("request magic mismatch".to_owned());
    }
    if u16::from_le_bytes([input[8], input[9]]) != BROKER_PROTOCOL_VERSION {
        return Err("request protocol version mismatch".to_owned());
    }
    if u16::from_le_bytes([input[10], input[11]]) != REQUEST_OPCODE {
        return Err("request opcode mismatch".to_owned());
    }
    if input[13..16] != [0u8; 3] {
        return Err("request reserved bytes must be zero".to_owned());
    }
    let drive = input[12].to_ascii_uppercase();
    if !drive.is_ascii_uppercase() {
        return Err("request drive letter must be ASCII A-Z".to_owned());
    }
    let mut nonce = [0u8; 16];
    nonce.copy_from_slice(&input[16..32]);
    Ok(BrokerRequest {
        drive_letter: drive,
        nonce,
    })
}

pub fn encode_response(response: &BrokerResponse) -> [u8; BROKER_RESPONSE_LEN] {
    let mut output = [0u8; BROKER_RESPONSE_LEN];
    output[0..8].copy_from_slice(RESPONSE_MAGIC);
    output[8..10].copy_from_slice(&BROKER_PROTOCOL_VERSION.to_le_bytes());
    output[10..12].copy_from_slice(&RESPONSE_OPCODE.to_le_bytes());
    output[12..16].copy_from_slice(&response.result.to_le_bytes());
    output[16..32].copy_from_slice(&response.nonce);
    output[32..36].copy_from_slice(&response.status.protection_status.to_le_bytes());
    output[36..40].copy_from_slice(&response.status.conversion_status.to_le_bytes());
    output
}

pub fn decode_response(input: &[u8], expected_nonce: &[u8; 16]) -> Result<BrokerResponse, String> {
    if input.len() != BROKER_RESPONSE_LEN {
        return Err(format!(
            "response length must be {BROKER_RESPONSE_LEN} bytes"
        ));
    }
    if &input[0..8] != RESPONSE_MAGIC {
        return Err("response magic mismatch".to_owned());
    }
    if u16::from_le_bytes([input[8], input[9]]) != BROKER_PROTOCOL_VERSION {
        return Err("response protocol version mismatch".to_owned());
    }
    if u16::from_le_bytes([input[10], input[11]]) != RESPONSE_OPCODE {
        return Err("response opcode mismatch".to_owned());
    }
    let mut nonce = [0u8; 16];
    nonce.copy_from_slice(&input[16..32]);
    if &nonce != expected_nonce {
        return Err("response nonce mismatch".to_owned());
    }
    Ok(BrokerResponse {
        result: u32::from_le_bytes(input[12..16].try_into().expect("fixed response field")),
        nonce,
        status: BitLockerStatus {
            protection_status: u32::from_le_bytes(
                input[32..36].try_into().expect("fixed response field"),
            ),
            conversion_status: u32::from_le_bytes(
                input[36..40].try_into().expect("fixed response field"),
            ),
        },
    })
}

pub fn success_response(nonce: [u8; 16], status: BitLockerStatus) -> BrokerResponse {
    BrokerResponse {
        result: RESULT_OK,
        nonce,
        status,
    }
}

pub fn failure_response(nonce: [u8; 16]) -> BrokerResponse {
    BrokerResponse {
        result: 1,
        nonce,
        status: BitLockerStatus {
            protection_status: u32::MAX,
            conversion_status: u32::MAX,
        },
    }
}

pub fn response_status(response: BrokerResponse) -> Result<BitLockerStatus, VolumeSecurityError> {
    if response.result != RESULT_OK {
        return Err(VolumeSecurityError::BrokerQueryFailed);
    }
    Ok(response.status)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_full_encryption_with_protection_on_is_protected() {
        assert_eq!(classify_bitlocker(1, 1), VolumeProtectionState::Protected);
        assert_eq!(classify_bitlocker(1, 2), VolumeProtectionState::Unprotected);
        assert_eq!(classify_bitlocker(0, 1), VolumeProtectionState::Unprotected);
    }

    #[test]
    fn unknown_provider_values_fail_closed() {
        assert_eq!(classify_bitlocker(2, 1), VolumeProtectionState::Unknown);
        assert_eq!(
            classify_bitlocker(u32::MAX, u32::MAX),
            VolumeProtectionState::Unknown
        );
    }

    #[test]
    fn request_round_trip_is_fixed_and_canonical() {
        let request = BrokerRequest {
            drive_letter: b'c',
            nonce: [0xA5; 16],
        };
        let encoded = encode_request(&request).expect("valid request");
        assert_eq!(encoded.len(), BROKER_REQUEST_LEN);
        assert_eq!(decode_request(&encoded).expect("decode").drive_letter, b'C');
        assert_eq!(
            encode_request(&decode_request(&encoded).unwrap()).unwrap(),
            encoded
        );
    }

    #[test]
    fn request_rejects_every_open_ended_field() {
        let request = BrokerRequest {
            drive_letter: b'C',
            nonce: [7; 16],
        };
        let valid = encode_request(&request).unwrap();
        assert!(decode_request(&valid[..31]).is_err());

        let mut invalid = valid;
        invalid[0] ^= 1;
        assert!(decode_request(&invalid).is_err());
        let mut invalid = valid;
        invalid[8] = 2;
        assert!(decode_request(&invalid).is_err());
        let mut invalid = valid;
        invalid[10] = 3;
        assert!(decode_request(&invalid).is_err());
        let mut invalid = valid;
        invalid[13] = 1;
        assert!(decode_request(&invalid).is_err());
        let mut invalid = valid;
        invalid[12] = b'1';
        assert!(decode_request(&invalid).is_err());
    }

    #[test]
    fn response_is_bound_to_request_nonce() {
        let response = success_response(
            [9; 16],
            BitLockerStatus {
                protection_status: 1,
                conversion_status: 1,
            },
        );
        let encoded = encode_response(&response);
        assert_eq!(encoded.len(), BROKER_RESPONSE_LEN);
        assert_eq!(decode_response(&encoded, &[9; 16]).unwrap(), response);
        assert!(decode_response(&encoded, &[8; 16]).is_err());
    }

    #[test]
    fn broker_failure_never_produces_a_status() {
        let response = failure_response([3; 16]);
        assert!(response_status(response).is_err());
    }
}
