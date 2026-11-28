"""Data lock: AES-256-GCM encryption for the bot's state and recorded data.

Key = scrypt(DATA_KEY passphrase, committed salt in datalock.salt). File format: MAGIC + 12-byte nonce + ciphertext+tag.
CLI (research helper):  DATA_KEY=... python datalock.py decrypt FILE [-o OUT]   (.jsonl.gz.enc → JSON lines)
"""
import argparse, gzip, os, sys
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"WRENC1"
SALT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datalock.salt")


class LockError(Exception):
    """Wrong key, tampered file or not an encrypted file."""


def read_salt(path=SALT_FILE):
    with open(path, "r", encoding="ascii") as f:
        return bytes.fromhex(f.read().strip())


@lru_cache(maxsize=4)
def derive_key(passphrase, salt):
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode("utf-8"))


def key_from_env(env=None):
    p = (env or os.environ).get("DATA_KEY", "").strip()
    return derive_key(p, read_salt()) if p else None


def encrypt(key, data):
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def decrypt(key, blob):
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 12 + 16:
        raise LockError("not an encrypted War Room file")
    nonce = blob[len(MAGIC):len(MAGIC) + 12]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + 12:], MAGIC)
    except Exception:
        raise LockError("cannot decrypt: wrong DATA_KEY or the file was changed (tampered)") from None


def write_atomic(path, data):
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_plain(path, key=None):
    """Bytes of a data/state file, decrypting .enc files and un-gzipping .gz payloads."""
    with open(path, "rb") as f:
        blob = f.read()
    if path.endswith(".enc"):
        if key is None:
            raise LockError("DATA_KEY is needed to read this file")
        blob = decrypt(key, blob)
    if path.endswith(".gz") or path.endswith(".gz.enc"):
        blob = gzip.decompress(blob)
    return blob


def main(argv=None):
    ap = argparse.ArgumentParser(description="Decrypt War Room state/data files (needs DATA_KEY in the environment)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decrypt")
    d.add_argument("file")
    d.add_argument("-o", "--out")
    a = ap.parse_args(argv)
    try:
        out = read_plain(a.file, key_from_env())
    except (LockError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if a.out:
        write_atomic(a.out, out)
    else:
        sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
