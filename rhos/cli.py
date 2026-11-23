"""Restricted router command shell. Input is parsed as RHOS commands only."""

from __future__ import annotations

import shlex
import sys
import getpass

from .config import ConfigError, Store
from .auth import AuthError, AuthStore


def parse_values(tokens: list[str]) -> dict[str, str]:
    values = {}
    for token in tokens:
        if "=" not in token:
            raise ConfigError(f"expected key=value, got {token!r}")
        key, value = token.split("=", 1)
        if not key or key in values:
            raise ConfigError(f"empty or duplicate parameter {key!r}")
        values[key] = value
    return values


def index_value(values: dict[str, str], collection: list[dict]) -> int:
    raw_index = values.get("numbers")
    if raw_index is None or not raw_index.isdigit():
        raise ConfigError("numbers= must be a nonnegative row index")
    index = int(raw_index)
    if index >= len(collection):
        raise ConfigError(f"row index {index} does not exist")
    return index


def execute(store: Store, command: str) -> str:
    tokens = shlex.split(command)
    if not tokens:
        return ""
    if tokens in (["/system", "print"], ["system", "print"]):
        return f"RouterHost OS 0.1.0\n{store.snapshot()['config']['system']['hostname']} revision={store.snapshot()['revision']}"
    if tokens in (["/interface", "print"], ["interface", "print"]):
        from .linux import interfaces
        return "\n".join(
            f"{row['name']:<16} {row['state']:<10} mtu={row['mtu']} mac={row['mac'] or '-'} "
            f"rx={row['rx'].get('bytes', 0)} tx={row['tx'].get('bytes', 0)}"
            for row in interfaces()
        )
    if tokens in (["/interface", "ethernet", "print"], ["interface", "ethernet", "print"]):
        from .linux import interfaces
        return "\n".join(
            f"{row['name']:<16} mac={row['mac'] or '-'} state={row['state']} mtu={row['mtu']}"
            for row in interfaces()
        )
    if tokens in (["/ip", "route", "print"], ["route", "print"]):
        snapshot = store.snapshot()
        config = snapshot.get("candidate", snapshot["config"])
        return "\n".join(
            f"{i:<4} {row['dst-address']:<22} via={row['gateway']} metric={row['metric']}"
            for i, row in enumerate(config["routes"])
        ) or "(no configured routes)"
    if tokens in (["/ip", "route", "active"], ["route", "active"]):
        from .linux import routes
        return "\n".join(
            f"{i:<4} {row.get('dst', '?'):<22} via={row.get('gateway', 'connected')} dev={row.get('dev', '?')}"
            for i, row in enumerate(routes())
        )
    if tokens in (["/ip", "address", "print"], ["address", "print"]):
        snapshot = store.snapshot()
        config = snapshot.get("candidate", snapshot["config"])
        return "\n".join(f"{i:<4} {item['address']:<20} {item['interface']}" for i, item in enumerate(config["addresses"])) or "(no configured addresses)"
    if tokens[:3] == ["/ip", "address", "add"] or tokens[:2] == ["address", "add"]:
        offset = 3 if tokens[0] == "/ip" else 2
        values = parse_values(tokens[offset:])
        if set(values) != {"address", "interface"}:
            raise ConfigError("address add requires address= and interface=")
        snap = store.snapshot()
        store.mutate(snap["revision"], "address_add", values)
        return "candidate address added"
    if tokens[:3] == ["/ip", "address", "remove"] or tokens[:2] == ["address", "remove"]:
        offset = 3 if tokens[0] == "/ip" else 2
        values = parse_values(tokens[offset:])
        snap = store.snapshot()
        config = snap.get("candidate", snap["config"])
        index = index_value(values, config["addresses"])
        store.mutate(snap["revision"], "address_remove", {"numbers": str(index)})
        return "candidate address removed"
    if tokens[:3] == ["/ip", "route", "add"] or tokens[:2] == ["route", "add"]:
        offset = 3 if tokens[0] == "/ip" else 2
        values = parse_values(tokens[offset:])
        if not {"dst-address", "gateway"} <= set(values) or set(values) - {"dst-address", "gateway", "metric"}:
            raise ConfigError("route add requires dst-address= and gateway=, optional metric=")
        snap = store.snapshot()
        store.mutate(snap["revision"], "route_add", values)
        return "candidate route added"
    if tokens[:3] == ["/ip", "route", "remove"] or tokens[:2] == ["route", "remove"]:
        offset = 3 if tokens[0] == "/ip" else 2
        values = parse_values(tokens[offset:])
        snap = store.snapshot()
        config = snap.get("candidate", snap["config"])
        index = index_value(values, config["routes"])
        store.mutate(snap["revision"], "route_remove", {"numbers": str(index)})
        return "candidate route removed"
    if tokens[:3] == ["/ip", "route", "set"] or tokens[:2] == ["route", "set"]:
        offset = 3 if tokens[0] == "/ip" else 2
        values = parse_values(tokens[offset:])
        snap = store.snapshot()
        config = snap.get("candidate", snap["config"])
        index = index_value(values, config["routes"])
        changes = {key: value for key, value in values.items() if key in {"dst-address", "gateway", "metric"}}
        if not changes:
            raise ConfigError("route set requires dst-address=, gateway=, or metric=")
        store.mutate(snap["revision"], "route_set", {"numbers": str(index), **changes})
        return "candidate route updated"
    if tokens in (["/system", "configuration", "diff"], ["system", "configuration", "diff"], ["configuration", "diff"]):
        import json
        return json.dumps(store.diff(), indent=2)
    if tokens in (["/system", "configuration", "commit"], ["configuration", "commit"]):
        raise ConfigError("use the interactive CLI to authenticate and confirm a commit")
    if tokens == ["quit"] or tokens == ["exit"]:
        raise EOFError
    raise ConfigError("unknown RHOS command. Try /interface print, /ip address print, /ip route print, /system print")


def main(argv: list[str] | None = None) -> int:
    import os
    import shlex

    argv = list(sys.argv[1:] if argv is None else argv)
    state_dir = os.getenv("RHOS_STATE_DIR", "/var/lib/rhos")
    if len(argv) >= 2 and argv[0] == "--state-dir":
        state_dir, argv = argv[1], argv[2:]
    elif argv and argv[0].startswith("--state-dir="):
        state_dir, argv = argv[0].split("=", 1)[1], argv[1:]
    store = Store(state_dir)
    if argv:
        try:
            command = " ".join(shlex.quote(token) for token in argv)
            print(execute(store, command))
            return 0
        except (ConfigError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    print("RouterHost OS 0.1.0\nType help or quit.")
    while True:
        try:
            line = input("admin@router01 > ")
            if line.strip() == "help":
                print("/interface print | /ip address print | /ip address add address=... interface=... | /ip route print | /ip route add dst-address=... gateway=... | /system print | /system configuration diff|commit")
                continue
            if line.strip() in ("/system configuration commit", "configuration commit"):
                snapshot = store.snapshot()
                if "candidate" not in snapshot:
                    print("no candidate configuration")
                    continue
                if not sys.stdin.isatty():
                    print("interactive commit confirmation requires a terminal")
                    continue
                username = input("Administrator name: ").strip()
                password = getpass.getpass("Administrator password: ")
                auth = AuthStore(store.directory)
                if not auth.verify(username, password):
                    print("authentication failed")
                    continue
                answer = input("Persist candidate configuration? [y/N] ")
                if answer.lower() != "y":
                    print("commit cancelled")
                    continue
                try:
                    committed = store.commit(snapshot["revision"])
                except Exception as exc:
                    print(f"commit failed: {exc}")
                    continue
                print(f"configuration persisted at revision {committed['revision']}")
                continue
            print(execute(store, line))
        except EOFError:
            print()
            return 0
        except (ConfigError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
