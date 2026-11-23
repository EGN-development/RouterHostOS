#!/bin/bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
    echo "Run this builder as root inside a disposable Debian 13 VM" >&2
    exit 1
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=$(realpath -m "${1:-$repo_dir/dist}")
required=(mmdebstrap qemu-img grub-install chroot losetup mount umount parted partprobe mkfs.vfat mkfs.ext4 blkid)
for tool in "${required[@]}"; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing builder dependency: $tool" >&2
        exit 1
    fi
done
if [[ $(dpkg --print-architecture) != amd64 ]]; then
    echo "This target currently supports amd64 only" >&2
    exit 1
fi

mkdir -p "$output_dir"
work_dir=$(mktemp -d)
loop_device=
mounted=()
cleanup() {
    for mountpoint in "${mounted[@]}"; do
        mountpoint -q "$mountpoint" 2>/dev/null && umount "$mountpoint" || true
    done
    [[ -n $loop_device ]] && losetup -d "$loop_device" 2>/dev/null || true
    rm -rf "$work_dir"
}
trap cleanup EXIT

raw="$output_dir/rhos-amd64.raw"
qcow2="$output_dir/rhos-amd64.qcow2"
root="$work_dir/root"
efi="$work_dir/efi"
truncate -s 8G "$raw"
loop_device=$(losetup --find --show --partscan "$raw")
parted -s "$loop_device" mklabel gpt mkpart primary fat32 1MiB 512MiB set 1 esp on mkpart primary ext4 512MiB 100%
partprobe "$loop_device"
mkfs.vfat "${loop_device}p1"
mkfs.ext4 -F "${loop_device}p2"
mkdir -p "$root" "$efi"
mount "${loop_device}p2" "$root"
mounted+=("$root")
mkdir -p "$root/boot/efi"
mount "${loop_device}p1" "$root/boot/efi"
mounted+=("$root/boot/efi")

mmdebstrap --mode=auto --variant=minbase --architectures=amd64 \
    --include=linux-image-amd64,grub-pc,systemd-sysv,python3,python3-venv,python3-pip,iproute2,nftables,ca-certificates,parted,grub-efi-amd64-bin,grub-pc-bin,grub-common,grub2-common \
    trixie "$root" http://deb.debian.org/debian

mount --bind /dev "$root/dev"
mounted+=("$root/dev")
mount -t proc proc "$root/proc"
mounted+=("$root/proc")
mount -t sysfs sysfs "$root/sys"
mounted+=("$root/sys")
mount --bind /run "$root/run"
mounted+=("$root/run")

install -d "$root/usr/lib/rhos"
install -m 0644 "$repo_dir"/rhos/*.py "$root/usr/lib/rhos/"
install -m 0644 "$repo_dir/pyproject.toml" "$root/usr/lib/rhos/pyproject.toml"
install -m 0644 "$repo_dir/requirements.txt" "$root/usr/lib/rhos/requirements.txt"
install -m 0644 "$repo_dir"/packaging/systemd/*.service "$root/etc/systemd/system/"
install -m 0644 "$repo_dir"/packaging/systemd/*.socket "$root/etc/systemd/system/"
install -m 0755 "$repo_dir/packaging/rhos-network-worker" "$root/usr/lib/rhos/rhos-network-worker"

root_uuid=$(blkid -s UUID -o value "${loop_device}p2")
efi_uuid=$(blkid -s UUID -o value "${loop_device}p1")
printf 'UUID=%s / ext4 errors=remount-ro 0 1\nUUID=%s /boot/efi vfat umask=0077 0 2\n' "$root_uuid" "$efi_uuid" > "$root/etc/fstab"

RHOS_DISK_DEVICE="$loop_device" chroot "$root" /bin/bash -euc '
    groupadd --system rhos
    useradd --system --gid rhos --home-dir /var/lib/rhos --create-home --shell /usr/sbin/nologin rhos
    python3 -m venv /usr/lib/rhos/venv
    /usr/lib/rhos/venv/bin/pip install --no-cache-dir /usr/lib/rhos
    systemctl enable rhos-api.service rhos-network.service rhos-network.socket
    systemctl set-default multi-user.target
    mkdir -p /etc/kernel/postinst.d
    printf "console=tty0 console=ttyS0,115200n8\\n" > /etc/kernel/cmdline
    update-initramfs -u -k all
    grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=RHOS --removable --no-nvram
    grub-install --target=i386-pc "$RHOS_DISK_DEVICE"
    update-grub
'

for mountpoint in "${mounted[@]}"; do umount "$mountpoint"; done
mounted=()
losetup -d "$loop_device"
loop_device=
qemu-img convert -f raw -O qcow2 "$raw" "$qcow2"
qemu-img check "$qcow2"
echo "Built $raw and $qcow2. Run boot-test.sh on a KVM-capable runner before distribution."
