from __future__ import annotations

import json
from pathlib import Path

import pytest
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)
from nacl.exceptions import CryptoError


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "xchacha20poly1305.json"


def _fixture_bytes() -> tuple[bytes, bytes, bytes, bytes, bytes]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["schemaVersion"] == 1
    assert fixture["algorithm"] == "XChaCha20-Poly1305-IETF"
    return tuple(
        bytes.fromhex(fixture[field])
        for field in ("keyHex", "nonceHex", "aadHex", "plaintextHex", "ciphertextHex")
    )


def test_versioned_xchacha_fixture_matches_libsodium() -> None:
    key, nonce, aad, plaintext, ciphertext = _fixture_bytes()
    assert crypto_aead_xchacha20poly1305_ietf_encrypt(
        plaintext, aad, nonce, key
    ) == ciphertext
    assert crypto_aead_xchacha20poly1305_ietf_decrypt(
        ciphertext, aad, nonce, key
    ) == plaintext


def test_versioned_xchacha_fixture_rejects_modified_aad_and_ciphertext() -> None:
    key, nonce, aad, _plaintext, ciphertext = _fixture_bytes()
    with pytest.raises(CryptoError):
        crypto_aead_xchacha20poly1305_ietf_decrypt(
            ciphertext, aad + b"!", nonce, key
        )
    corrupted = bytearray(ciphertext)
    corrupted[-1] ^= 1
    with pytest.raises(CryptoError):
        crypto_aead_xchacha20poly1305_ietf_decrypt(
            bytes(corrupted), aad, nonce, key
        )
