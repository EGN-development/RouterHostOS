"""Start the RHOS API service."""

import argparse
import time

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
    app = create_app(args.state_dir)
    store = app.state.rhos_store
    try:
        store.recover_expired_confirmed()
    except Exception:
        raise SystemExit("expired confirmed commit recovery failed. Restore network connectivity and retry startup.")
    def recover_pending():
        while True:
            time.sleep(1)
            try:
                store.recover_expired_confirmed()
            except Exception:
                # Keep retrying so a transient worker failure does not discard rollback intent.
                continue
    import threading
    threading.Thread(target=recover_pending, daemon=True).start()
    host, port = args.listen.rsplit(":", 1)
    uvicorn.run(app, host=host, port=int(port))


if __name__ == "__main__":
    main()
