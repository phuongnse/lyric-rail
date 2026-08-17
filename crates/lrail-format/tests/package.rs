use std::{
    collections::HashSet,
    fs,
    io::{Read, Seek, SeekFrom, Write},
};

use lrail_format::{
    AssetRequest, ContentEncoding, Error, HEADER_SIZE, Header, PackageReader, PackageRequest,
    inspect_package, pack, pack_for_vault, rewrap_package_for_vaults, verify_package,
    verify_package_with_vault, verify_package_with_vault_candidates,
};
use serde_json::json;
use tempfile::tempdir;

fn fixture() -> (tempfile::TempDir, PackageRequest) {
    let directory = tempdir().unwrap();
    let media = directory.path().join("media.bin");
    let lyrics = directory.path().join("lyrics.json");
    fs::write(&media, vec![0x5a; 2_500_000]).unwrap();
    fs::write(&lyrics, br#"{"lines":[{"text":"Xin chao"}]}"#).unwrap();
    let request = PackageRequest {
        metadata: json!({"song": {"title": "Fixture"}}),
        producer: "LyricRail tests".into(),
        minimum_player_version: "0.8.0".into(),
        assets: vec![
            AssetRequest {
                logical_name: "media/main.mp4".into(),
                path: media,
                media_type: "video/mp4".into(),
                kind: "media".into(),
                track_name: None,
                language: None,
                default: true,
                content_encoding: ContentEncoding::Identity,
            },
            AssetRequest {
                logical_name: "lyrics/timing.json".into(),
                path: lyrics,
                media_type: "application/json".into(),
                kind: "lyrics".into(),
                track_name: None,
                language: Some("vi".into()),
                default: false,
                content_encoding: ContentEncoding::Identity,
            },
        ],
    };
    (directory, request)
}

fn asset_ciphertext(path: &std::path::Path) -> Vec<u8> {
    let bytes = fs::read(path).unwrap();
    let header_bytes: [u8; HEADER_SIZE] = bytes[..HEADER_SIZE].try_into().unwrap();
    let header = Header::decode(&header_bytes).unwrap();
    let start = (header.envelope_offset + header.envelope_length) as usize;
    bytes[start..header.manifest_offset as usize].to_vec()
}

#[test]
fn os_vault_slot_is_device_bound_and_recovery_remains_portable() {
    let (directory, request) = fixture();
    let output = directory.path().join("vault.lrail");
    let vault_master = [0x42_u8; 32];
    pack_for_vault(
        &request,
        &output,
        &vault_master,
        Some(b"correct horse battery staple"),
    )
    .unwrap();

    let inspection = inspect_package(&output).unwrap();
    assert_eq!(
        inspection.key_mechanisms,
        vec!["os-vault-v1", "recovery-v1"]
    );
    assert!(
        verify_package_with_vault(&output, &vault_master)
            .unwrap()
            .valid
    );
    assert!(matches!(
        PackageReader::open_with_vault(&output, &[0x24_u8; 32]),
        Err(Error::KeyUnwrap)
    ));
    assert!(
        verify_package(&output, b"correct horse battery staple")
            .unwrap()
            .valid
    );
}

#[test]
fn vault_rewrap_is_dual_key_safe_and_preserves_media_ciphertext_byte_for_byte() {
    let (directory, request) = fixture();
    let original = directory.path().join("original.lrail");
    let dual = directory.path().join("dual.lrail");
    let new_only = directory.path().join("new-only.lrail");
    let rejected = directory.path().join("rejected.lrail");
    let old_master = [0x42_u8; 32];
    let new_master = [0x24_u8; 32];
    let wrong_master = [0x81_u8; 32];
    let recovery = b"correct horse battery staple";
    pack_for_vault(&request, &original, &old_master, Some(recovery)).unwrap();
    let original_ciphertext = asset_ciphertext(&original);

    let dual_report = rewrap_package_for_vaults(
        &original,
        &dual,
        &[&old_master],
        &[&old_master, &new_master],
    )
    .unwrap();
    assert_eq!(dual_report.vault_slots, 2);
    assert_eq!(dual_report.preserved_non_vault_slots, 1);
    assert_eq!(asset_ciphertext(&dual), original_ciphertext);
    assert!(verify_package_with_vault(&dual, &old_master).unwrap().valid);
    assert!(verify_package_with_vault(&dual, &new_master).unwrap().valid);
    assert!(
        verify_package_with_vault_candidates(&dual, &[&wrong_master, &new_master])
            .unwrap()
            .valid
    );
    assert!(verify_package(&dual, recovery).unwrap().valid);

    let new_report = rewrap_package_for_vaults(
        &dual,
        &new_only,
        &[&old_master, &new_master],
        &[&new_master],
    )
    .unwrap();
    assert_eq!(new_report.vault_slots, 1);
    assert_eq!(new_report.preserved_non_vault_slots, 1);
    assert_eq!(asset_ciphertext(&new_only), original_ciphertext);
    assert!(
        verify_package_with_vault(&new_only, &new_master)
            .unwrap()
            .valid
    );
    assert!(matches!(
        verify_package_with_vault(&new_only, &old_master),
        Err(Error::KeyUnwrap)
    ));
    assert!(verify_package(&new_only, recovery).unwrap().valid);

    assert!(
        rewrap_package_for_vaults(&new_only, &rejected, &[&wrong_master], &[&old_master],).is_err()
    );
    assert!(!rejected.exists());

    // Rotation only changes the small envelope and manifest. It never creates
    // another encoded copy of the media payload inside a package.
    assert!(dual_report.new_package_bytes - dual_report.old_package_bytes < 1024);
    assert!(new_report.old_package_bytes - new_report.new_package_bytes < 1024);
}

#[test]
fn package_round_trip_and_random_access() {
    let (directory, request) = fixture();
    let output = directory.path().join("fixture.lrail");
    pack(&request, &output, b"correct horse battery staple").unwrap();
    let inspection = inspect_package(&output).unwrap();
    assert_eq!(inspection.key_mechanisms, vec!["recovery-v1"]);

    let report = verify_package(&output, b"correct horse battery staple").unwrap();
    assert!(report.valid);
    assert_eq!(report.asset_count, 2);
    assert_eq!(report.chunk_count, 4);

    let mut reader = PackageReader::open(&output, b"correct horse battery staple").unwrap();
    let bytes = reader
        .read_asset_range("media/main.mp4", 1_000_000, 200_000)
        .unwrap();
    assert_eq!(bytes.len(), 200_000);
    assert!(bytes.iter().all(|byte| *byte == 0x5a));
}

#[test]
fn fresh_packages_have_distinct_identity_ciphertext_and_nonces() {
    let (directory, request) = fixture();
    let first = directory.path().join("first.lrail");
    let second = directory.path().join("second.lrail");
    let vault_master = [0x42_u8; 32];
    pack_for_vault(&request, &first, &vault_master, None).unwrap();
    pack_for_vault(&request, &second, &vault_master, None).unwrap();

    let first_bytes = fs::read(&first).unwrap();
    let second_bytes = fs::read(&second).unwrap();
    assert_ne!(first_bytes, second_bytes);
    assert_ne!(&first_bytes[16..32], &second_bytes[16..32]);
    assert_ne!(&first_bytes[64..88], &second_bytes[64..88]);

    let first_reader = PackageReader::open_with_vault(&first, &vault_master).unwrap();
    let second_reader = PackageReader::open_with_vault(&second, &vault_master).unwrap();
    assert_ne!(
        first_reader.manifest.package_id,
        second_reader.manifest.package_id
    );
    let first_nonces = first_reader
        .manifest
        .assets
        .iter()
        .flat_map(|asset| asset.chunks.iter().map(|chunk| chunk.nonce.clone()))
        .collect::<HashSet<_>>();
    let second_nonces = second_reader
        .manifest
        .assets
        .iter()
        .flat_map(|asset| asset.chunks.iter().map(|chunk| chunk.nonce.clone()))
        .collect::<HashSet<_>>();
    assert_eq!(first_nonces.len(), 4);
    assert_eq!(second_nonces.len(), 4);
    assert!(first_nonces.is_disjoint(&second_nonces));
}

#[test]
fn appended_truncated_and_reserved_header_data_are_rejected() {
    let (directory, request) = fixture();
    let source = directory.path().join("source.lrail");
    pack(&request, &source, b"correct horse battery staple").unwrap();
    let original = fs::read(&source).unwrap();

    let appended = directory.path().join("appended.lrail");
    let mut appended_bytes = original.clone();
    appended_bytes.push(0);
    fs::write(&appended, appended_bytes).unwrap();
    assert!(inspect_package(&appended).is_err());

    let truncated = directory.path().join("truncated.lrail");
    fs::write(&truncated, &original[..original.len() - 1]).unwrap();
    assert!(inspect_package(&truncated).is_err());

    let reserved = directory.path().join("reserved.lrail");
    let mut reserved_bytes = original;
    reserved_bytes[127] = 1;
    fs::write(&reserved, reserved_bytes).unwrap();
    assert!(inspect_package(&reserved).is_err());
}

#[test]
fn wrong_password_and_tamper_are_rejected() {
    let (directory, request) = fixture();
    let output = directory.path().join("fixture.lrail");
    pack(&request, &output, b"correct horse battery staple").unwrap();
    assert!(matches!(
        PackageReader::open(&output, b"this password is definitely wrong"),
        Err(Error::KeyUnwrap)
    ));

    let inspection = inspect_package(&output).unwrap();
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&output)
        .unwrap();
    let tamper_offset = 512_u64.min(inspection.package_length - 1);
    file.seek(SeekFrom::Start(tamper_offset)).unwrap();
    let mut byte = [0_u8; 1];
    file.read_exact(&mut byte).unwrap();
    byte[0] ^= 0x80;
    file.seek(SeekFrom::Start(tamper_offset)).unwrap();
    file.write_all(&byte).unwrap();
    file.sync_all().unwrap();
    assert!(verify_package(&output, b"correct horse battery staple").is_err());
}

#[test]
fn unsafe_logical_names_are_rejected() {
    let (directory, mut request) = fixture();
    request.assets[0].logical_name = "../escape.mp4".into();
    let output = directory.path().join("fixture.lrail");
    assert!(matches!(
        pack(&request, &output, b"correct horse battery staple"),
        Err(Error::InvalidAsset(_))
    ));
}

#[test]
fn duplicate_assets_and_oversized_metadata_are_rejected_before_output() {
    let (directory, mut request) = fixture();
    request.assets[1].logical_name = request.assets[0].logical_name.clone();
    let duplicate_output = directory.path().join("duplicate.lrail");
    assert!(matches!(
        pack(&request, &duplicate_output, b"correct horse battery staple"),
        Err(Error::InvalidAsset(_))
    ));
    assert!(!duplicate_output.exists());

    let (_second_directory, mut oversized) = fixture();
    oversized.metadata = json!({"value": "x".repeat(1024 * 1024 + 1)});
    let oversized_output = directory.path().join("oversized.lrail");
    assert!(matches!(
        pack(
            &oversized,
            &oversized_output,
            b"correct horse battery staple"
        ),
        Err(Error::InvalidFormat(_))
    ));
    assert!(!oversized_output.exists());
}

#[test]
fn deeply_nested_metadata_is_rejected() {
    let (directory, mut request) = fixture();
    let mut metadata = serde_json::Value::Null;
    for _ in 0..66 {
        metadata = json!([metadata]);
    }
    request.metadata = metadata;
    let output = directory.path().join("deep.lrail");
    assert!(matches!(
        pack(&request, &output, b"correct horse battery staple"),
        Err(Error::InvalidFormat(_))
    ));
    assert!(!output.exists());
}
