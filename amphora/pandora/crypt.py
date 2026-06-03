"""Blowfish ECB encryption used by the Pandora JSON API.

Pandora encrypts request bodies (and some response fields, e.g. the sync
time) with Blowfish in ECB mode, hex-encoded.  The partner credentials ship
with two keys: one for encrypting outgoing data and one for decrypting
incoming data.
"""

from __future__ import annotations

import binascii

# Blowfish lives in ``decrepit`` on recent ``cryptography`` releases but is
# still importable from ``primitives`` for a while.  Try both.
try:  # cryptography >= 43
    from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
except ImportError:  # pragma: no cover - older cryptography
    from cryptography.hazmat.primitives.ciphers.algorithms import Blowfish

from cryptography.hazmat.primitives.ciphers import Cipher, modes

_BLOCK_SIZE = 8  # Blowfish block size in bytes


class PandoraCrypt:
    """Encrypt/decrypt helpers bound to a partner's key pair."""

    def __init__(self, encrypt_key: str, decrypt_key: str) -> None:
        self._encrypt_key = encrypt_key.encode("ascii")
        self._decrypt_key = decrypt_key.encode("ascii")

    def _cipher(self, key: bytes) -> Cipher:
        return Cipher(Blowfish(key), modes.ECB())

    @staticmethod
    def _pad(data: bytes) -> bytes:
        pad = _BLOCK_SIZE - (len(data) % _BLOCK_SIZE)
        if pad == _BLOCK_SIZE:
            return data
        # Pandora tolerates trailing NUL bytes after the JSON payload.
        return data + (b"\x00" * pad)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt ``plaintext`` and return a lowercase hex string."""
        data = self._pad(plaintext.encode("utf-8"))
        enc = self._cipher(self._encrypt_key).encryptor()
        return binascii.hexlify(enc.update(data) + enc.finalize()).decode("ascii")

    def decrypt(self, hex_data: str) -> bytes:
        raw = binascii.unhexlify(hex_data)
        dec = self._cipher(self._decrypt_key).decryptor()
        return dec.update(raw) + dec.finalize()

    def decrypt_sync_time(self, hex_data: str) -> int:
        """Decode the encrypted ``syncTime`` returned by ``partnerLogin``.

        The decrypted payload is a 4-byte seed followed by the unix sync time
        as ASCII digits, NUL-padded to a block boundary.
        """
        raw = self.decrypt(hex_data)[4:]
        digits = bytes(b for b in raw if 0x30 <= b <= 0x39)
        return int(digits)
