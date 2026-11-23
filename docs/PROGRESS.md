# Development status

## Implemented in this increment

- Python control-plane package with FastAPI API, CLI parser, config validation, candidate state, revision checks, persistence, and history.
- Linux interface/address/route observation using structured `ip -j` output.
- Explicitly gated address/route/IPv4-forwarding/nftables apply adapter.
- Small management page backed by the live API and persisted configuration.
- Unit and API tests.
- Cross-process serialization and stale revision checks for configuration mutations.
- Candidate changes persist through restart, and CLI displays candidate addresses.
- First administrator bootstrap, scrypt password storage, signed short-lived sessions, protected configuration reads, CSRF checks on mutations, and CLI commit authentication.
- nftables policy generation for RHOS-owned IPv4 NAT and default-drop forward filtering, plus desired-state address/route reconciliation with compensating rollback.
- Live Ethernet inventory joins link details, counters, and addresses, exposed through the API and RouterOS-style CLI.
- Web login, protected dashboard, and initial local-only administrator setup command.
- Debian host installation script and hardened loopback-only unprivileged systemd service unit.
- Configuration commit compensates kernel network state back to the previous desired configuration when apply or persistence fails.
- Dedicated capability-limited root network worker with a group-restricted Unix socket protocol. API process receives no network capability.
- Debian amd64 raw/qcow2 builder and QEMU serial boot smoke script.

## Verified

- `python -m unittest discover -s tests -v`: 19 tests passed, including simulated apply and persistence failures.
- Worker protocol tests cover operation allowlisting and worker error handling.
- API login throttling and browser security response headers.
- `python -m compileall -q rhos tests`: passed.
- `git diff --check`: passed.
- Started the real Uvicorn service and made HTTP requests to system information, dashboard, and configuration endpoints. All returned expected responses.
- Live interface/route collection requires the `ip` tool. The environment provides it, but host network apply was not run.

The environment does not include nftables, QEMU, Rust/Cargo, or a working Go compiler command. The container is unprivileged, so host network apply and kernel forwarding integration are not verifiable here. The differential apply test uses a controlled command runner and does not prove kernel behavior. The FastAPI test client emitted a Starlette deprecation warning about its installed httpx integration, while the tests passed.

## Not implemented

Actual bootable image build and QEMU boot verification, TLS termination, granular RBAC/user management, crash-safe confirmed commit timer, crash-atomic network rollback, FRR, DHCP/DNS, IPv6, WireGuard, advanced firewall, production frontend, signed updates, and multi-VM integration tests.

## Next step

Run `python -m unittest discover -s tests -v`, review the installer and unit, then add a dedicated privileged network worker and package into a minimal Debian-based image. Validate service startup and boot under QEMU in an environment that provides boot tooling, QEMU, nftables, and isolated network namespace capabilities. Do not describe RHOS as a bootable OS until that boot test passes.
