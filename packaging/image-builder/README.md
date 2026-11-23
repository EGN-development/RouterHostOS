# Bootable image builder

The image target runs on a dedicated Debian 13 x86_64 builder with rootful access, `mmdebstrap`, `linux-image-amd64`, `qemu-system-x86`, `qemu-utils`, GRUB EFI/PC modules, loop devices, and partitioning tools. It assembles a Debian root filesystem, installs RHOS into a venv, installs GRUB, and emits a raw disk image plus a qcow2 conversion.

The builder writes only to the supplied output directory. Run it on a disposable builder host. The image uses a serial console and loopback-only management by default. On first boot, create an administrator from the local console with `rhos-admin create`.

The script checks all tooling and root privileges before work. It is not safe to run inside a developer workstation because it uses loop devices and mounts. A successful image build still needs a separate QEMU boot test before the image can be called bootable.
