"""Authenticated streaming encryption for off-host backup archives."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"RCBKPA01"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
HEADER = struct.Struct(">8s12sQ")


def _load_key(path: Path) -> bytes:
    raw = path.read_bytes().strip()
    candidates = [raw]
    try:
        candidates.append(base64.urlsafe_b64decode(raw))
    except Exception:
        pass
    try:
        candidates.append(bytes.fromhex(raw.decode("ascii")))
    except Exception:
        pass
    for key in candidates:
        if len(key) == 32:
            return key
    raise ValueError("backup encryption key must decode to exactly 32 bytes")


def encrypt(source: Path, target: Path, key_path: Path) -> dict[str, object]:
    key = _load_key(key_path)
    nonce = os.urandom(NONCE_SIZE)
    source_size = source.stat().st_size
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    digest = hashlib.sha256()

    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_stream, target.open("wb") as output_stream:
        output_stream.write(HEADER.pack(MAGIC, nonce, source_size))
        while chunk := input_stream.read(CHUNK_SIZE):
            digest.update(chunk)
            output_stream.write(encryptor.update(chunk))
        output_stream.write(encryptor.finalize())
        output_stream.write(encryptor.tag)

    os.chmod(target, 0o600)
    return {
        "plaintext_size": source_size,
        "plaintext_sha256": digest.hexdigest(),
        "archive_size": target.stat().st_size,
    }


def verify(source: Path, key_path: Path) -> dict[str, object]:
    key = _load_key(key_path)
    total_size = source.stat().st_size
    if total_size < HEADER.size + TAG_SIZE:
        raise ValueError("encrypted backup archive is truncated")

    with source.open("rb") as stream:
        magic, nonce, plaintext_size = HEADER.unpack(stream.read(HEADER.size))
        if magic != MAGIC:
            raise ValueError("encrypted backup archive has an unknown format")
        stream.seek(-TAG_SIZE, os.SEEK_END)
        tag = stream.read(TAG_SIZE)
        ciphertext_size = total_size - HEADER.size - TAG_SIZE
        stream.seek(HEADER.size)

        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        digest = hashlib.sha256()
        produced = 0
        remaining = ciphertext_size
        while remaining:
            chunk = stream.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise ValueError("encrypted backup archive ended early")
            remaining -= len(chunk)
            plaintext = decryptor.update(chunk)
            produced += len(plaintext)
            digest.update(plaintext)
        tail = decryptor.finalize()
        produced += len(tail)
        digest.update(tail)

    if produced != plaintext_size:
        raise ValueError(
            f"decrypted backup size mismatch: expected {plaintext_size}, got {produced}"
        )
    return {
        "plaintext_size": plaintext_size,
        "plaintext_sha256": digest.hexdigest(),
        "archive_size": total_size,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    encrypt_parser = subparsers.add_parser("encrypt")
    encrypt_parser.add_argument("--source", type=Path, required=True)
    encrypt_parser.add_argument("--target", type=Path, required=True)
    encrypt_parser.add_argument("--key-file", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--source", type=Path, required=True)
    verify_parser.add_argument("--key-file", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "encrypt":
        result = encrypt(arguments.source, arguments.target, arguments.key_file)
    else:
        result = verify(arguments.source, arguments.key_file)
    for key, value in sorted(result.items()):
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
