#!/usr/bin/env python3
"""Generate the VAPID key pair that signs your morning push notifications.

    pip install py-vapid
    python genkeys.py

Run it once. Put the public key in the repository **variable**
VAPID_PUBLIC_KEY (the browser has to read it, so it isn't a secret) and the
private key in the repository **secret** VAPID_PRIVATE_KEY.

Why this file exists rather than a one-liner: py_vapid exposes the keys as
cryptography objects, not as base64 strings, and the convenience methods that
used to return them were removed. Web Push wants the public key as the raw
uncompressed P-256 point and the private key as the raw 32-byte scalar, both
base64url without padding — which is what this does.
"""

import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def main() -> int:
    v = Vapid01()
    v.generate_keys()

    public = b64(v.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint))
    private = b64(v.private_key.private_numbers().private_value.to_bytes(32, "big"))

    # Shapes are fixed for P-256; a wrong one fails silently at push time.
    assert len(public) == 87 and public.startswith("B"), "unexpected public key"
    assert len(private) == 43, "unexpected private key"

    print()
    print("VAPID_PUBLIC_KEY   (repository VARIABLE)")
    print(public)
    print()
    print("VAPID_PRIVATE_KEY  (repository SECRET — do not share or screenshot)")
    print(private)
    print()
    print("Settings → Secrets and variables → Actions.")
    print("Public goes on the Variables tab, private on the Secrets tab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
