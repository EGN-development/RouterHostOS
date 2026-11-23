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

## Verified

- `python -m unittest discover -s tests -v`: 16 tests passed after authentication and network policy coverage.
- `python -m compileall -q rhos tests`: passed.
- `git diff --check`: passed.
- Started the real Uvicorn service and made HTTP requests to system information, dashboard, and configuration endpoints. All returned expected responses.
- Live interface/route collection requires the `ip` tool. The environment provides it, but host network apply was not run.

The environment does not include nftables, QEMU, Rust/Cargo, or a working Go compiler command. The container is unprivileged, so host network apply and kernel forwarding integration are not verifiable here. The differential apply test uses a controlled command runner and does not prove kernel behavior. The FastAPI test client emitted a Starlette deprecation warning about its installed httpx integration, while the tests passed.

## Not implemented

Bootable image, installer, TLS termination, granular RBAC/user management, safe-mode confirmed commit timer, atomic network rollback after partial apply, FRR, DHCP/DNS, IPv6, WireGuard, advanced firewall, production frontend, signed updates, and multi-VM integration tests.

## Next step

Run `python -m unittest discover -s tests -v`, fix failures, then package into a minimal Debian-based image and validate boot under QEMU in an environment that provides a kernel, boot tooling, QEMU, nftables, and isolated network namespace capabilities. Do not describe RHOS as a bootable OS until that boot test passes.
