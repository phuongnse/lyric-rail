#![no_main]

use std::{fs, io::Write, sync::OnceLock};

use libfuzzer_sys::fuzz_target;
use lrail_format::{
    AssetRequest, ContentEncoding, PackageReader, PackageRequest, inspect_package, pack_for_vault,
};
use serde_json::json;

const MAX_FUZZ_INPUT: usize = 64 * 1024 * 1024;
const FUZZ_VAULT: [u8; 32] = [0u8; 32];

fn valid_seed_package() -> &'static [u8] {
    static PACKAGE: OnceLock<Vec<u8>> = OnceLock::new();
    PACKAGE.get_or_init(|| {
        let directory = tempfile::tempdir().expect("create fuzz seed directory");
        let asset_path = directory.path().join("asset.bin");
        fs::write(&asset_path, (0..=255).cycle().take(4097).collect::<Vec<_>>())
            .expect("write fuzz seed asset");
        let package_path = directory.path().join("seed.lrail");
        let request = PackageRequest {
            metadata: json!({"title": "Fuzz seed"}),
            producer: "LyricRail fuzz target".into(),
            minimum_player_version: "0.8.0".into(),
            assets: vec![AssetRequest {
                logical_name: "media/seed.bin".into(),
                path: asset_path,
                media_type: "application/octet-stream".into(),
                kind: "fuzz-seed".into(),
                track_name: None,
                language: None,
                default: true,
                content_encoding: ContentEncoding::Identity,
            }],
        };
        pack_for_vault(&request, &package_path, &FUZZ_VAULT, None)
            .expect("pack valid fuzz seed");
        fs::read(package_path).expect("read valid fuzz seed")
    })
}

fuzz_target!(|data: &[u8]| {
    if data.len() > MAX_FUZZ_INPUT {
        return;
    }
    let mut package_bytes = valid_seed_package().to_vec();
    for mutation in data.get(8..).unwrap_or_default().chunks_exact(5).take(1024) {
        let index = u32::from_le_bytes(mutation[..4].try_into().unwrap()) as usize
            % package_bytes.len();
        package_bytes[index] ^= mutation[4];
    }

    let Ok(mut package) = tempfile::NamedTempFile::new() else {
        return;
    };
    if package.write_all(&package_bytes).is_err() || package.flush().is_err() {
        return;
    }

    let _ = inspect_package(package.path());
    if let Ok(mut reader) = PackageReader::open_with_vault(package.path(), &FUZZ_VAULT) {
        let length = reader.manifest.assets[0].plaintext_length;
        let selector = data
            .get(..8)
            .and_then(|bytes| bytes.try_into().ok())
            .map(u64::from_le_bytes)
            .unwrap_or_default();
        let offset = selector % length;
        let count = usize::try_from((selector >> 32) % (length - offset + 1)).unwrap_or(0);
        let _ = reader.read_asset_range("media/seed.bin", offset, count);
    }

    // Exercise the early length/header rejection path independently of the
    // authenticated baseline mutations above.
    if let Ok(mut raw) = tempfile::NamedTempFile::new()
        && raw.write_all(data).is_ok()
        && raw.flush().is_ok()
    {
        let _ = inspect_package(raw.path());
    }
});
