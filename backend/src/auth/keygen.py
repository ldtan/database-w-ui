"""Write the RS256 keypair the app signs its tokens with.

    uv run python -m src.auth.keygen

One command so that first-run, CI and the deploy notes all say the same thing. It
exists because the alternative was a README paragraph of `openssl` invocations that
three files quoted slightly differently, and because two of them said a keypair was
already committed — `keys/` is gitignored, so it never was. A fresh clone could not
start the app *or* run a single test: `auth/helpers.py` reads both PEMs at import
time, so the failure is a `FileNotFoundError` out of `create_app()` and out of
`tests/conftest.py` with it, including `test_health.py`, whose whole point is not
needing anything.

Refuses to overwrite an existing pair unless asked. Replacing the private key
invalidates every access and refresh token signed with the old one — every session
ends at once — which is a fine thing to do deliberately and a miserable thing to do
because a setup script was re-run.
"""
import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.core.config import settings

# 2048 is the floor RS256 is worth using at, and what the deploy has. Nothing here
# needs more, and a larger key costs every token verification.
_KEY_SIZE = 2048


def generate(private_path: Path, public_path: Path, *, force: bool = False) -> bool:
    """Write a keypair. Returns False if one was already there and was kept."""
    if not force and (private_path.exists() or public_path.exists()):
        return False

    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE)
    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            # Unencrypted: the process reads it at import with no passphrase to
            # supply. Its protection is the filesystem, and on Render a Secret File.
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing pair (ends every signed-in session)",
    )
    args = parser.parse_args()

    # The configured paths, so this writes where the app will look rather than
    # where this file guesses. A `.env` pointing elsewhere is honoured for free.
    private_path = Path(settings.RSA_PRIVATE_KEY_PATH)
    public_path = Path(settings.RSA_PUBLIC_KEY_PATH)

    if generate(private_path, public_path, force=args.force):
        print(f"wrote {private_path} and {public_path}")
    else:
        print(
            f"{private_path} already exists — keeping it. "
            "Pass --force to replace the pair (this ends every signed-in session)."
        )


if __name__ == "__main__":
    main()
