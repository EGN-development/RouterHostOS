"""Linux observation and opt-in apply adapter. No shell interpolation is used."""

from __future__ import annotations

import json
import subprocess
from typing import Any


def run(argv: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(argv, input=input_text, text=True, capture_output=True, check=False, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"{argv[0]} exited with {result.returncode}")
    return result.stdout


def interfaces() -> list[dict[str, Any]]:
    data = json.loads(run(["ip", "-j", "-details", "link", "show"]))
    return [
        {
            "name": item.get("ifname", ""),
            "state": item.get("operstate", "UNKNOWN"),
            "mtu": item.get("mtu"),
            "mac": item.get("address"),
            "rx_bytes": item.get("stats64", {}).get("rx", {}).get("bytes"),
            "tx_bytes": item.get("stats64", {}).get("tx", {}).get("bytes"),
        }
        for item in data
    ]


def addresses() -> list[dict[str, Any]]:
    return json.loads(run(["ip", "-j", "address", "show"]))


def routes() -> list[dict[str, Any]]:
    return json.loads(run(["ip", "-j", "route", "show", "table", "all"]))


def apply_config(config: dict[str, Any]) -> None:
    nat = config["firewall"]["nat"]
    script = ["destroy table ip rhos", "table ip rhos {"]
    if nat:
        script += [" chain postrouting {", "  type nat hook postrouting priority srcnat; policy accept;"]
        for rule in nat:
            script.append(f'  ip saddr {rule["source"]} oifname "{rule["out-interface"]}" masquerade')
        script.append(" }")
    script.append("}")
    run(["nft", "-c", "-f", "-"], input_text="\n".join(script) + "\n")
    original_forwarding = open("/proc/sys/net/ipv4/ip_forward").read().strip()
    original_addresses = json.loads(run(["ip", "-j", "address", "show"]))
    original_routes = json.loads(run(["ip", "-j", "route", "show", "table", "main"]))
    try:
        run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
        for item in config["addresses"]:
            run(["ip", "address", "replace", item["address"], "dev", item["interface"]])
        for item in config["routes"]:
            run(["ip", "route", "replace", item["dst-address"], "via", item["gateway"], "metric", str(item["metric"])])
        run(["nft", "-f", "-"], input_text="\n".join(script) + "\n")
    except (RuntimeError, OSError):
        for route in original_routes:
            if route.get("dst") and route.get("gateway") and route.get("dev"):
                subprocess.run(["ip", "route", "replace", route["dst"], "via", route["gateway"], "dev", route["dev"]], capture_output=True, timeout=15)
        for interface in original_addresses:
            dev = interface.get("ifname")
            if not dev:
                continue
            for info in interface.get("addr_info", []):
                if info.get("scope") == "global" and info.get("local") and info.get("prefixlen") is not None:
                    cidr = f'{info["local"]}/{info["prefixlen"]}'
                    subprocess.run(["ip", "address", "replace", cidr, "dev", dev], capture_output=True, timeout=15)
        run(["sysctl", "-w", f"net.ipv4.ip_forward={original_forwarding}"])
        raise
