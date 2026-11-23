"""Validated, revisioned configuration shared by API and CLI."""

from __future__ import annotations

import copy
import ipaddress
import json
import os
import tempfile
import threading
import time
import fcntl
from pathlib import Path
from typing import Any


DEFAULT: dict[str, Any] = {
    "system": {"hostname": "router01"},
    "interfaces": [],
    "addresses": [],
    "routes": [],
    "firewall": {"forward": [], "nat": []},
}


class ConfigError(ValueError):
    pass


def validate(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict) or set(config) != set(DEFAULT):
        raise ConfigError("configuration must contain system, interfaces, addresses, routes, and firewall")
    system = config["system"]
    if not isinstance(system, dict) or set(system) != {"hostname"}:
        raise ConfigError("system must contain only hostname")
    hostname = system["hostname"]
    if not isinstance(hostname, str) or not hostname or len(hostname) > 63 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-." for c in hostname):
        raise ConfigError("hostname must be 1-63 ASCII letters, digits, dots, or hyphens")

    if not isinstance(config["interfaces"], list):
        raise ConfigError("interfaces must be a list")
    interface_names: set[str] = set()
    for item in config["interfaces"]:
        if not isinstance(item, dict) or set(item) != {"name", "enabled", "mtu"}:
            raise ConfigError("interface entries require name, enabled, and mtu")
        name = item["name"]
        if not isinstance(name, str) or not name or len(name) > 15 or not name.replace("_", "a").replace("-", "a").isalnum():
            raise ConfigError("invalid interface name")
        if name in interface_names:
            raise ConfigError("interface names must be unique")
        interface_names.add(name)
        if type(item["enabled"]) is not bool or type(item["mtu"]) is not int or not 576 <= item["mtu"] <= 9000:
            raise ConfigError("interface enabled must be boolean and mtu must be 576..9000")

    if not isinstance(config["addresses"], list):
        raise ConfigError("addresses must be a list")
    seen_addresses: set[tuple[str, str]] = set()
    for item in config["addresses"]:
        if not isinstance(item, dict) or set(item) != {"address", "interface"}:
            raise ConfigError("address entries require address and interface")
        try:
            ipaddress.ip_interface(item["address"])
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"invalid address {item.get('address')!r}") from exc
        interface = item["interface"]
        if not isinstance(interface, str) or not interface or len(interface) > 15:
            raise ConfigError("invalid address interface")
        key = (item["address"], interface)
        if key in seen_addresses:
            raise ConfigError("duplicate address")
        seen_addresses.add(key)

    if not isinstance(config["routes"], list):
        raise ConfigError("routes must be a list")
    seen_routes: set[tuple[str, str, int]] = set()
    for item in config["routes"]:
        if not isinstance(item, dict) or set(item) != {"dst-address", "gateway", "metric"}:
            raise ConfigError("route entries require dst-address, gateway, and metric")
        try:
            ipaddress.ip_network(item["dst-address"], strict=False)
            gateway = ipaddress.ip_address(item["gateway"])
        except (ValueError, TypeError) as exc:
            raise ConfigError("invalid route destination or gateway") from exc
        if type(item["metric"]) is not int or not 0 <= item["metric"] <= 2**31 - 1:
            raise ConfigError("route metric must be a nonnegative integer")
        if gateway.version != ipaddress.ip_network(item["dst-address"], strict=False).version:
            raise ConfigError("route gateway and destination address families differ")
        key = (item["dst-address"], item["gateway"], item["metric"])
        if key in seen_routes:
            raise ConfigError("duplicate route")
        seen_routes.add(key)

    firewall = config["firewall"]
    if not isinstance(firewall, dict) or set(firewall) != {"forward", "nat"} or not isinstance(firewall["nat"], list) or not isinstance(firewall["forward"], list):
        raise ConfigError("firewall must contain forward and nat lists")
    for rule in firewall["forward"]:
        if not isinstance(rule, dict) or set(rule) != {"in-interface", "out-interface", "action"}:
            raise ConfigError("forward rules require in-interface, out-interface, and action")
        for field in ("in-interface", "out-interface"):
            value = rule[field]
            if not isinstance(value, str) or not value or len(value) > 15 or not value.replace("_", "a").replace("-", "a").isalnum():
                raise ConfigError(f"invalid forward rule {field}")
        if rule["action"] not in ("accept", "drop"):
            raise ConfigError("forward action must be accept or drop")
    nat_keys = set()
    for item in firewall["nat"]:
        if not isinstance(item, dict) or set(item) != {"out-interface", "source"}:
            raise ConfigError("NAT entries require out-interface and source")
        if not isinstance(item["out-interface"], str) or not item["out-interface"] or len(item["out-interface"]) > 15:
            raise ConfigError("invalid NAT output interface")
        try:
            ipaddress.ip_network(item["source"], strict=False)
        except (ValueError, TypeError) as exc:
            raise ConfigError("invalid NAT source network") from exc
        key = (item["out-interface"], item["source"])
        if key in nat_keys:
            raise ConfigError("duplicate NAT rule")
        nat_keys.add(key)
    return config


class Store:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "config.json"
        self.lock = threading.RLock()
        self.lock_path = self.directory / ".lock"
        self.lock_path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.lock_path, 0o600)
        self.state = {"revision": 0, "config": copy.deepcopy(DEFAULT), "history": []}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                validate(loaded["config"])
                if type(loaded["revision"]) is not int or loaded["revision"] < 0 or not isinstance(loaded["history"], list):
                    raise ValueError("invalid state metadata")
                self.state = loaded
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ConfigError(f"cannot load persisted configuration: {exc}") from exc

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._reload()
            return copy.deepcopy(self.state)

    class _FileLock:
        def __init__(self, path: Path):
            self.file = open(path, "a+")

        def __enter__(self):
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX)
            return self

        def __exit__(self, *_):
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()

    def _locked(self):
        return self._FileLock(self.lock_path)

    def _reload(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text())
            validate(loaded["config"])
            if "candidate" in loaded:
                validate(loaded["candidate"])
            pending = loaded.get("pending_confirmed")
            if pending is not None:
                if not isinstance(pending, dict) or type(pending.get("revision")) is not int or type(pending.get("expires_at")) not in (int, float):
                    raise ValueError("invalid pending confirmed commit metadata")
                validate(pending.get("previous"))
                validate(pending.get("candidate"))
            if type(loaded["revision"]) is not int or loaded["revision"] < 0 or not isinstance(loaded["history"], list):
                raise ValueError("invalid state metadata")
            for entry in loaded["history"]:
                if not isinstance(entry, dict) or type(entry.get("revision")) is not int or not isinstance(entry.get("timestamp"), int):
                    raise ValueError("invalid history entry")
                validate(entry.get("config"))
            self.state = loaded
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConfigError(f"cannot load persisted configuration: {exc}") from exc

    def replace_candidate(self, config: Any, expected_revision: int) -> dict[str, Any]:
        validate(config)
        with self.lock:
            with self._locked():
                self._reload()
                if expected_revision != self.state["revision"]:
                    raise Conflict(f"revision conflict: expected {expected_revision}, current {self.state['revision']}")
                if self.state.get("pending_confirmed"):
                    raise Conflict("configuration is locked while confirmed commit is pending")
                if self.state.get("pending_confirmed"):
                    raise Conflict("a confirmed commit is awaiting confirmation")
                self.state["candidate"] = copy.deepcopy(config)
                self._persist()
                return self.snapshot()

    def commit_confirmed(self, expected_revision: int, timeout: int) -> dict[str, Any]:
        if type(timeout) is not int or not 30 <= timeout <= 3600:
            raise ConfigError("confirmed commit timeout must be between 30 and 3600 seconds")
        with self.lock:
            with self._locked():
                self._reload()
                if expected_revision != self.state["revision"]:
                    raise Conflict(f"revision conflict: expected {expected_revision}, current {self.state['revision']}")
                if self.state.get("pending_confirmed"):
                    raise Conflict("a confirmed commit is already pending")
                if os.getenv("RHOS_ALLOW_NETWORK_APPLY") != "1":
                    raise ConfigError("network apply is disabled for this service profile")
                from .linux import apply_via_worker
                previous = copy.deepcopy(self.state["config"])
                candidate = copy.deepcopy(self.state.get("candidate", previous))
                validate(candidate)
                apply_via_worker(previous)
                try:
                    apply_via_worker(candidate)
                except Exception:
                    apply_via_worker(previous)
                    raise
                original_state = copy.deepcopy(self.state)
                self.state["revision"] += 1
                self.state["config"] = candidate
                self.state["pending_confirmed"] = {
                    "revision": self.state["revision"],
                    "previous": previous,
                    "candidate": candidate,
                    "expires_at": time.time() + timeout,
                }
                try:
                    self._persist()
                except OSError:
                    self.state = original_state
                    try:
                        apply_via_worker(previous)
                    except Exception as rollback_error:
                        raise ConfigError(f"confirmed commit persistence failed and network rollback failed: {rollback_error}")
                    raise
                return self.snapshot()

    def confirm(self, revision: int) -> dict[str, Any]:
        with self.lock:
            with self._locked():
                self._reload()
                pending = self.state.get("pending_confirmed")
                if not pending or pending["revision"] != revision:
                    raise Conflict("no matching confirmed commit is pending")
                if pending["expires_at"] <= time.time():
                    self._restore_pending(pending)
                    raise Conflict("confirmed commit expired and was rolled back")
                self.state["history"] = (self.state["history"] + [{
                    "revision": revision,
                    "timestamp": int(time.time()),
                    "config": copy.deepcopy(pending["candidate"]),
                }])[-50:]
                self.state.pop("pending_confirmed", None)
                self.state.pop("candidate", None)
                self._persist()
                return self.snapshot()

    def recover_expired_confirmed(self) -> bool:
        with self.lock:
            with self._locked():
                self._reload()
                pending = self.state.get("pending_confirmed")
                if not pending or pending["expires_at"] > time.time():
                    return False
                self._restore_pending(pending)
                return True

    def _restore_pending(self, pending: dict[str, Any]) -> None:
        from .linux import apply_via_worker
        apply_via_worker(pending["previous"])
        original_state = copy.deepcopy(self.state)
        self.state["config"] = copy.deepcopy(pending["previous"])
        self.state["candidate"] = copy.deepcopy(pending["candidate"])
        self.state["revision"] += 1
        self.state.pop("pending_confirmed", None)
        try:
            self._persist()
        except OSError:
            self.state = original_state
            try:
                apply_via_worker(pending["candidate"])
            except Exception as rollback_error:
                raise ConfigError(f"rollback persistence failed and restoring candidate network failed: {rollback_error}")
            raise

    def mutate(self, expected_revision: int, operation: str, values: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            with self._locked():
                self._reload()
                if expected_revision != self.state["revision"]:
                    raise Conflict(f"revision conflict: expected {expected_revision}, current {self.state['revision']}")
                if self.state.get("pending_confirmed"):
                    raise Conflict("configuration is locked while confirmed commit is pending")
                candidate = copy.deepcopy(self.state.get("candidate", self.state["config"]))
                if operation == "address_add":
                    candidate["addresses"].append({"address": values["address"], "interface": values["interface"]})
                elif operation == "address_remove":
                    candidate["addresses"].pop(int(values["numbers"]))
                elif operation == "route_add":
                    candidate["routes"].append({"dst-address": values["dst-address"], "gateway": values["gateway"], "metric": int(values.get("metric", 100))})
                elif operation == "route_remove":
                    candidate["routes"].pop(int(values["numbers"]))
                elif operation == "route_set":
                    route = candidate["routes"][int(values["numbers"])]
                    route["dst-address"] = values.get("dst-address", route["dst-address"])
                    route["gateway"] = values.get("gateway", route["gateway"])
                    route["metric"] = int(values.get("metric", route["metric"]))
                elif operation == "interface_set":
                    interface = next((entry for entry in candidate["interfaces"] if entry["name"] == values["name"]), None)
                    if interface is None:
                        raise ConfigError(f"configured interface {values['name']!r} was not found")
                    if "mtu" in values:
                        interface["mtu"] = int(values["mtu"])
                    if "enabled" in values:
                        if values["enabled"] not in ("true", "false"):
                            raise ConfigError("enabled must be true or false")
                        interface["enabled"] = values["enabled"] == "true"
                elif operation == "interface_add":
                    candidate["interfaces"].append({"name": values["name"], "enabled": values.get("enabled", "true").lower() == "true", "mtu": int(values.get("mtu", 1500))})
                elif operation == "nat_add":
                    candidate["firewall"]["nat"].append({"out-interface": values["out-interface"], "source": values["source"]})
                else:
                    raise ConfigError("unsupported operation")
                validate(candidate)
                self.state["candidate"] = candidate
                self._persist()
                return self.snapshot()

    def commit(self, expected_revision: int, apply: bool = False) -> dict[str, Any]:
        with self.lock:
            with self._locked():
                self._reload()
                if expected_revision != self.state["revision"]:
                    raise Conflict(f"revision conflict: expected {expected_revision}, current {self.state['revision']}")
                if self.state.get("pending_confirmed"):
                    raise Conflict("configuration is locked while confirmed commit is pending")
                candidate = copy.deepcopy(self.state.get("candidate", self.state["config"]))
                validate(candidate)
                if candidate == self.state["config"] and not apply:
                    self.state.pop("candidate", None)
                    self._persist()
                    return self.snapshot()
                if apply:
                    if os.getenv("RHOS_ALLOW_NETWORK_APPLY") != "1":
                        raise ConfigError("network apply is disabled for this service profile")
                    from .linux import apply_via_worker
                    apply_via_worker(self.state["config"])
                    try:
                        apply_via_worker(candidate)
                    except Exception:
                        try:
                            apply_via_worker(self.state["config"])
                        except Exception as rollback_error:
                            raise ConfigError(f"network apply failed and automatic rollback also failed: {rollback_error}")
                        raise
                previous = self.state
                next_revision = previous["revision"] + 1
                self.state = {
                    "revision": next_revision,
                    "config": candidate,
                    "history": (previous["history"] + [{"revision": next_revision, "timestamp": int(time.time()), "config": candidate}])[-50:],
                }
                try:
                    self._persist()
                except OSError:
                    self.state = previous
                    if apply:
                        from .linux import apply_via_worker
                        try:
                            apply_via_worker(previous["config"])
                        except Exception as rollback_error:
                            raise ConfigError(f"configuration persistence failed and network rollback also failed: {rollback_error}")
                    raise
                return self.snapshot()

    def rollback(self, revision: int, expected_revision: int) -> dict[str, Any]:
        with self.lock:
            with self._locked():
                self._reload()
                if expected_revision != self.state["revision"]:
                    raise Conflict(f"revision conflict: expected {expected_revision}, current {self.state['revision']}")
                if self.state.get("pending_confirmed"):
                    raise Conflict("configuration is locked while confirmed commit is pending")
                old = next((entry["config"] for entry in self.state["history"] if entry["revision"] == revision), None)
                if old is None:
                    raise ConfigError("requested revision is not in retained history")
                self.state["candidate"] = copy.deepcopy(old)
                self._persist()
                return self.snapshot()

    def diff(self) -> dict[str, Any]:
        with self.lock:
            self._reload()
            running = self.state["config"]
            candidate = self.state.get("candidate", running)
            return {"revision": self.state["revision"], "changed": candidate != running, "running": copy.deepcopy(running), "candidate": copy.deepcopy(candidate)}

    def _persist(self) -> None:
        data = json.dumps(self.state, indent=2, sort_keys=True)
        fd, temp_name = tempfile.mkstemp(prefix=".config-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, self.path)
            directory_fd = os.open(self.directory, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


class Conflict(ConfigError):
    pass
