"""Start the RHOS API service."""

import argparse

import uvicorn

from .api import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1:8080")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--allow-network-apply", action="store_true")
    args = parser.parse_args()
    if args.allow_network_apply:
        import os
        os.environ["RHOS_ALLOW_NETWORK_APPLY"] = "1"
    host, port = args.listen.rsplit(":", 1)
    uvicorn.run(create_app(args.state_dir), host=host, port=int(port))


if __name__ == "__main__":
    main()
