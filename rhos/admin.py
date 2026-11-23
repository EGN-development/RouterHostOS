"""Local-only administrator bootstrap helper."""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from .auth import AuthError, AuthStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rhos-admin")
    parser.add_argument("action", choices=["create"])
    parser.add_argument("--state-dir", default=os.getenv("RHOS_STATE_DIR", "/var/lib/rhos"))
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("administrator bootstrap requires a local interactive terminal")
    auth = AuthStore(args.state_dir)
    username = input("Administrator name: ").strip()
    password = getpass.getpass("New password (14+ chars): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("passwords do not match", file=sys.stderr)
        return 2
    try:
        auth.bootstrap(username, password)
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"administrator {username!r} created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
