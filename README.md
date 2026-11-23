# RouterHost OS (RHOS)

RouterHost OS is an early Linux router appliance project. The current repository contains a small Python control-plane prototype, not a bootable router operating system. Linux remains the forwarding plane. A single validated configuration store is shared by the CLI and HTTP API.

## Current prototype

- Persistent JSON configuration with schema validation, revision history, and optimistic revision checks.
- API and interactive CLI use the same configuration service.
- Read-only interface and route inventory on Linux.
- Optional application of IPv4 forwarding, addresses, static routes, and an RHOS-owned nftables forward/NAT policy. Applying host networking is disabled unless explicitly enabled.
- Server-rendered management page.

The prototype does not yet provide confirmed commits, FRR, DHCP, VPN, a hardened service sandbox, or an operating-system image. Do not expose the management API to untrusted networks. Do not enable host networking on a machine whose routes you rely on.

## Requirements

Python 3.11 or newer, Linux, and the Python packages in `requirements.txt`. To apply networking, install `iproute2` and `nftables`, run RHOS with appropriate privileges, and explicitly set `RHOS_APPLY_NETWORK=1`. The apply adapter is not a substitute for an isolated integration test.

## Run

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
RHOS_STATE_DIR=/var/lib/rhos python -m rhos --listen 127.0.0.1:8080
```

Create the first administrator in a local terminal:

```sh
python -m rhos.admin create --state-dir /var/lib/rhos
```

Open `http://127.0.0.1:8080/login`. The prototype binds to loopback by default. Authenticated API endpoints are under `/api/v1`. The browser session cookie is marked Secure, so use HTTPS for browser login. Put the service behind a trusted TLS terminator before binding it beyond loopback. Keep the reverse proxy and RHOS host on a trusted management network.

CLI:

```sh
python -m rhos.cli --state-dir /var/lib/rhos system print
python -m rhos.cli --state-dir /var/lib/rhos route print
python -m rhos.cli --state-dir /var/lib/rhos address add address=192.0.2.1/24 interface=eth1
python -m rhos.cli --state-dir /var/lib/rhos route add dst-address=198.51.100.0/24 gateway=192.0.2.2
```

CLI changes update the candidate configuration only. Interactive commit checks the local administrator password and asks for confirmation. API mutations require an administrator session and CSRF token. Applying host networking requires the explicit environment switch.

## API

- `GET /api/v1/system`
- `GET /api/v1/interfaces`
- `GET /api/v1/ip/addresses`
- `GET /api/v1/ip/routes`
- `GET /api/v1/configuration`
- `PUT /api/v1/configuration` with `If-Match: <revision>`
- `POST /api/v1/configuration/commit` with `{"revision": 1, "apply": false}`

## Development

```sh
python -m unittest discover -s tests -v
```

See `docs/ARCHITECTURE.md` for boundaries and `docs/PROGRESS.md` for verified status and next steps. No bootable QCOW2/RAW artifact is produced by this prototype.
