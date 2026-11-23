#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root" >&2
    exit 1
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${PYTHON_BIN:-/usr/bin/python3}

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-venv python3-pip iproute2 nftables systemd ca-certificates

if ! getent group rhos >/dev/null 2>&1; then
    groupadd --system rhos
fi
if ! id rhos >/dev/null 2>&1; then
    useradd --system --gid rhos --home-dir /var/lib/rhos --create-home --shell /usr/sbin/nologin rhos
fi

install -d -o root -g root -m 0755 /usr/lib/rhos
install -d -o root -g root -m 0755 /usr/lib/rhos/rhos
install -m 0644 "$repo_dir"/rhos/*.py /usr/lib/rhos/rhos/
install -m 0644 "$repo_dir"/requirements.txt /usr/lib/rhos/requirements.txt
install -m 0644 "$repo_dir"/pyproject.toml /usr/lib/rhos/pyproject.toml
install -m 0755 "$repo_dir"/packaging/rhos-network-worker /usr/lib/rhos/rhos-network-worker
"$python_bin" -m venv /usr/lib/rhos/venv
/usr/lib/rhos/venv/bin/pip install --disable-pip-version-check --requirement /usr/lib/rhos/requirements.txt
install -m 0644 "$repo_dir"/packaging/systemd/*.service /etc/systemd/system/
install -m 0644 "$repo_dir"/packaging/systemd/*.socket /etc/systemd/system/
install -d -o rhos -g rhos -m 0700 /var/lib/rhos
systemctl daemon-reload
systemctl enable --now rhos-network.service rhos-network.socket rhos-api.service
systemctl --no-pager --full status rhos-api.service
