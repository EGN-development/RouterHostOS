#!/bin/bash
set -euo pipefail

image=${1:?Usage: boot-test.sh path/to/rhos-amd64.qcow2}
for tool in qemu-system-x86_64 timeout grep; do
    command -v "$tool" >/dev/null 2>&1 || { echo "Missing test dependency: $tool" >&2; exit 1; }
done
[[ -f $image ]] || { echo "Image does not exist: $image" >&2; exit 1; }
output=$(mktemp)
trap 'rm -f "$output"' EXIT
set +e
timeout 90 qemu-system-x86_64 \
    -machine q35,accel=tcg \
    -cpu max \
    -m 2048 \
    -smp 2 \
    -drive "file=$image,if=virtio,format=qcow2" \
    -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
    -nographic -serial mon:stdio -no-reboot >"$output" 2>&1
status=$?
set -e
if ! grep -q 'Reached target.*Multi-User System' "$output"; then
    cat "$output"
    echo "RHOS did not reach the multi-user target (qemu exit $status)" >&2
    exit 1
fi
if ! grep -q 'rhos-api.service' "$output"; then
    cat "$output"
    echo "RHOS API service did not report startup in serial output" >&2
    exit 1
fi
echo "QEMU boot reached multi-user and RHOS API service was observed."
