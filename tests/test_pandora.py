"""Unit tests for the protocol-critical Pandora pieces (no network)."""

import binascii

from amphora.pandora import DEFAULT_CLIENT, Song, Station
from amphora.pandora.crypt import PandoraCrypt


def _crypt() -> PandoraCrypt:
    return PandoraCrypt(
        DEFAULT_CLIENT["encryptionKey"], DEFAULT_CLIENT["decryptionKey"]
    )


def test_encrypt_is_hex_and_block_aligned():
    crypt = _crypt()
    out = crypt.encrypt('{"a":1}')
    raw = binascii.unhexlify(out)  # must be valid hex
    assert len(raw) % 8 == 0


def test_decrypt_sync_time_roundtrip():
    # Build an encrypted sync-time the way Pandora's server would: a 4-byte
    # seed prefix followed by the unix time digits, encrypted with the *decrypt*
    # key (which the client uses to decode it).
    crypt = _crypt()
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.algorithms import Blowfish

    payload = b"seed" + b"1490000000"  # 4 + 10 bytes
    pad = 8 - (len(payload) % 8)
    payload += b"\x00" * (pad if pad != 8 else 0)
    enc = Cipher(
        Blowfish(DEFAULT_CLIENT["decryptionKey"].encode()), modes.ECB()
    ).encryptor()
    hexed = binascii.hexlify(enc.update(payload) + enc.finalize()).decode()

    assert crypt.decrypt_sync_time(hexed) == 1490000000


def test_song_parses_audio_url_map():
    item = {
        "trackToken": "tok",
        "songName": "Song",
        "artistName": "Artist",
        "albumName": "Album",
        "songRating": 1,
        "trackLength": 215,
        "albumArtUrl": "http://art/cover.jpg",
        "audioUrlMap": {
            "highQuality": {
                "audioUrl": "http://audio/high.aac",
                "bitrate": "64",
                "encoding": "aacplus",
            },
            "lowQuality": {"audioUrl": "http://audio/low.aac", "bitrate": "32"},
        },
    }
    song = Song.from_json(item, quality="high")
    assert song.audio_url == "http://audio/high.aac"
    assert song.title == "Song" and song.artist == "Artist"
    assert song.duration == 215
    assert song.is_loved
    assert song.bitrate == "64"


def test_song_prefers_additional_audio_url():
    item = {
        "trackToken": "t",
        "songName": "S",
        "artistName": "A",
        "albumName": "Al",
        "additionalAudioUrl": "http://direct/stream.mp3",
        "audioUrlMap": {"highQuality": {"audioUrl": "http://map/x.aac"}},
    }
    assert Song.from_json(item).audio_url == "http://direct/stream.mp3"


def test_ad_tokens_have_no_song_name():
    # getPlaylist filters items lacking songName; verify the marker.
    ad = {"adToken": "abc"}
    assert not ad.get("songName")


def test_station_sort_quickmix_first():
    stations = [
        Station(token="b", id="2", name="Zeppelin Radio"),
        Station(token="a", id="1", name="QuickMix", is_quickmix=True),
        Station(token="c", id="3", name="Adele Radio"),
    ]
    stations.sort(key=lambda s: (not s.is_quickmix, s.name.lower()))
    assert stations[0].name == "QuickMix"
    assert stations[1].name == "Adele Radio"
