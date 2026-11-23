"""Linux observation and opt-in apply adapter. No shell interpolation is used."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any


_STATE_PATH = Path(os.getenv("RHOS_NETWORK_STATE", "/var/lib/rhos-network/applied.json"))


def run(argv: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(argv, input=input_text, text=True, capture_output=True, check=False, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"{argv[0]} exited with {result.returncode}")
    return result.stdout


def interfaces() -> list[dict[str, Any]]:
    data = json.loads(run(["ip", "-j", "-details", "link", "show"]))
    address_data = json.loads(run(["ip", "-j", "address", "show"]))
    addresses_by_link = {
        item.get("ifname"): [
            {"address": address.get("local"), "prefix_length": address.get("prefixlen"), "family": address.get("family"), "scope": address.get("scope")}
            for address in item.get("addr_info", [])
        ]
        for item in address_data
    }
    result = []
    for item in data:
        result.append({
            "name": item.get("ifname", ""),
            "state": item.get("operstate", "UNKNOWN"),
            "mtu": item.get("mtu"),
            "mac": item.get("address"),
            "kind": item.get("linkinfo", {}).get("info_kind", "ether"),
            "addresses": addresses_by_link.get(item.get("ifname"), []),
            "rx": item.get("stats64", {}).get("rx", {}),
            "tx": item.get("stats64", {}).get("tx", {}),
        })
    return result


def addresses() -> list[dict[str, Any]]:
    return json.loads(run(["ip", "-j", "address", "show"]))


def routes() -> list[dict[str, Any]]:
    return json.loads(run(["ip", "-j", "route", "show", "table", "all"]))


def firewall_script(config: dict[str, Any]) -> str:
    firewall = config["firewall"]
    lines = [
        "delete table inet rhos_filter",
        "delete table ip rhos_nat",
        "table inet rhos_filter {",
        " chain forward {",
        "  type filter hook forward priority filter; policy drop;",
        "  ct state established,related accept",
    ]
    for rule in firewall["forward"]:
        lines.append(
            f'  iifname "{rule["in-interface"]}" oifname "{rule["out-interface"]}" {rule["action"]}'
        )
    lines += [" }", "}"]
    lines.append("table ip rhos_nat {")
    if firewall["nat"]:
        lines += [" chain postrouting {", "  type nat hook postrouting priority srcnat; policy accept;"]
        for rule in firewall["nat"]:
            lines.append(f'  ip saddr {rule["source"]} oifname "{rule["out-interface"]}" masquerade')
        lines.append(" }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _ensure_rhos_tables() -> None:
    listing = json.loads(run(["nft", "-j", "list", "tables"]))
    existing = {(item.get("family"), item.get("name")) for item in listing.get("nftables", []) if "table" in item for item in [item["table"]]}
    create = []
    for family, name in (("inet", "rhos_filter"), ("ip", "rhos_nat")):
        if (family, name) not in existing:
            create.append(f"add table {family} {name}")
    if create:
        run(["nft", "-f", "-"], input_text="\n".join(create) + "\n")


def _load_applied_state(path: Path) -> dict[str, list[list[Any]]]:
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return {"addresses": [], "routes": []}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read RHOS applied network state: {exc}") from exc
    if not state:
        return {"addresses": [], "routes": []}
    if (not isinstance(state, dict) or set(state) != {"addresses", "routes"}
            or not isinstance(state["addresses"], list) or not isinstance(state["routes"], list)
            or any(not isinstance(item, list) or len(item) != 2 or not all(isinstance(value, str) for value in item) for item in state["addresses"])
            or any(not isinstance(item, list) or len(item) != 3 or not isinstance(item[0], str) or not isinstance(item[1], str) or type(item[2]) is not int for item in state["routes"])):
        raise RuntimeError("RHOS applied network state is malformed; refusing to reconcile host networking")
    return state


def _save_applied_state(path: Path, state: dict[str, list[list[Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".applied-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def apply_config(config: dict[str, Any], *, state_path: Path | None = None) -> None:
    script = firewall_script(config)
    run(["nft", "-c", "-f", "-"], input_text=script)
    path = state_path or _STATE_PATH
    applied = _load_applied_state(path)
    owned_addresses = {tuple(item) for item in applied["addresses"]}
    owned_routes = {tuple(item) for item in applied["routes"]}
    desired_addresses = {(item["interface"], item["address"]) for item in config["addresses"]}
    desired_routes = {(item["dst-address"], item["gateway"], item["metric"]) for item in config["routes"]}
    current_addresses = json.loads(run(["ip", "-j", "address", "show"]))
    current_routes = json.loads(run(["ip", "-j", "route", "show", "table", "main"]))
    saved_forwarding = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
    current_address_set = {
        (dev, f'{info["local"]}/{info["prefixlen"]}')
        for link in current_addresses
        if (dev := link.get("ifname"))
        for info in link.get("addr_info", [])
        if info.get("family") == "inet" and info.get("scope") == "global"
    }
    existing_links = {entry.get("ifname"): entry for entry in current_addresses if entry.get("ifname")}
    current_link_details = json.loads(run(["ip", "-j", "-details", "link", "show"]))
    current_link_details = {entry.get("ifname"): entry for entry in current_link_details if entry.get("ifname")}
    desired_interfaces = {item["name"]: item for item in config["interfaces"]}
    for name, item in desired_interfaces.items():
        if name not in existing_links:
            raise RuntimeError(f"configured interface {name!r} does not exist")
    _ensure_rhos_tables()
    current_route_set = {
        (route.get("dst", "default"), route["gateway"], int(route.get("metric", 0)))
        for route in current_routes
        if route.get("gateway")
    }
    try:
        for name, item in desired_interfaces.items():
            current = current_link_details[name]
            if current.get("mtu") != item["mtu"]:
                run(["ip", "link", "set", "dev", name, "mtu", str(item["mtu"])])
            state = "up" if item["enabled"] else "down"
            if current.get("operstate", "UNKNOWN").lower() != ("up" if item["enabled"] else "down"):
                run(["ip", "link", "set", "dev", name, state])
        for interface, address in owned_addresses - desired_addresses:
            if (interface, address) in current_address_set:
                run(["ip", "address", "del", address, "dev", interface])
        for interface, address in desired_addresses - owned_addresses:
            if (interface, address) not in current_address_set:
                run(["ip", "address", "add", address, "dev", interface])
        for destination, gateway, metric in owned_routes - desired_routes:
            if (destination, gateway, metric) in current_route_set:
                run(["ip", "route", "del", destination, "via", gateway, "metric", str(metric)])
        for destination, gateway, metric in desired_routes - owned_routes:
            if (destination, gateway, metric) not in current_route_set:
                run(["ip", "route", "add", destination, "via", gateway, "metric", str(metric)])
        forwarding = "1" if config["addresses"] or config["routes"] or config["firewall"]["forward"] else saved_forwarding
        run(["sysctl", "-w", f"net.ipv4.ip_forward={forwarding}"])
        run(["nft", "-f", "-"], input_text=script)
        _save_applied_state(path, {
            "addresses": [list(item) for item in sorted(desired_addresses)],
            "routes": [list(item) for item in sorted(desired_routes)],
        })
    except (RuntimeError, OSError):
        for interface, address in desired_addresses - owned_addresses:
            subprocess.run(["ip", "address", "del", address, "dev", interface], capture_output=True, timeout=15)
        for interface, address in owned_addresses - desired_addresses:
            subprocess.run(["ip", "address", "add", address, "dev", interface], capture_output=True, timeout=15)
        for destination, gateway, metric in desired_routes - owned_routes:
            subprocess.run(["ip", "route", "del", destination, "via", gateway, "metric", str(metric)], capture_output=True, timeout=15)
        for destination, gateway, metric in owned_routes - desired_routes:
            subprocess.run(["ip", "route", "add", destination, "via", gateway, "metric", str(metric)], capture_output=True, timeout=15)
        subprocess.run(["sysctl", "-w", f"net.ipv4.ip_forward={saved_forwarding}"], capture_output=True, timeout=15)
        raise


def apply_via_worker(config: dict[str, Any], socket_path: str = "/run/rhos/network.sock") -> None:
    request = json.dumps({"operation": "apply", "config": config}, separators=(",", ":")).encode() + b"\n"
    if len(request) > 1024 * 1024:
        raise RuntimeError("network configuration exceeds worker request limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30)
        client.connect(socket_path)
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while len(response) <= 4096:
            block = client.recv(4097 - len(response))
            if not block:
                break
            response.extend(block)
            if b"\n" in block:
                break
    if len(response) > 4096:
        raise RuntimeError("network worker returned an oversized response")
    try:
        result = json.loads(bytes(response).split(b"\n", 1)[0])
    except (ValueError, TypeError) as exc:
        raise RuntimeError("network worker returned an invalid response") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = result.get("error", "network apply failed") if isinstance(result, dict) else "network worker returned an invalid response"
        raise RuntimeError(detail)
