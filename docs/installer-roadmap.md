# Installer Roadmap

Where the installer stands and what is left. P0 (reliability) and P1
(archinstall parity) are done and verified in QEMU.

## Housekeeping

- Add the Playwright GUI walkthrough (dry-run API, mocked disks) as a script
  and run it in CI next to the unit tests.
- Add a QEMU install smoke test to CI for at least one layout.

## P2 — beyond archinstall

Done, covered by unit tests and a dry run, but not yet verified in QEMU or on
hardware: snapshots, NVIDIA driver, Secure Boot, TPM2 unlock, fingerprint
(fprintd), Flatpak, gaming tweaks, locale suggestion, partition bar, log
upload, and the text installer boot entry.

The Server persona (key-only SSH, firewalld, Cockpit, Netdata, fail2ban,
update downloads, serial console, static addresses) is covered by unit tests
and a GUI dry run, but no server install has been booted in QEMU yet.

Still open:

- **Fingerprint enrollment during installation.** fprintd is installed, but
  fingers are enrolled afterwards in System Settings; enrolling in the live
  session needs fprintd on the ISO and a GUI flow for touching the sensor.
- **Snapshot boot entries for systemd-boot and Limine** (for example
  `limine-snapper-sync`, which is in the AUR).
- **Secure Boot hardening:** unified kernel images so the initramfs is
  signed, Limine config enrollment, and GRUB support.
- **QEMU verification** of Secure Boot (OVMF with Setup Mode) and TPM2
  (`swtpm`) installs.

## Known gaps and unverified paths

- **Live desktop networking** uses Archiso's iwd/systemd-networkd, so the
  Plasma panel has no network applet; Wi-Fi is set up on the installer's
  network screen.
- **Partition shrinking** is offered only by the GUI; the text installers
  can replay it from a saved configuration (`shrink_partition`,
  `shrink_size`). Never tested against a real Windows installation.
- **Boot art** is a placeholder paw print until the protogen visor artwork
  is approved.

- **Windows dual boot** never tested against a real Windows install; only the
  os-prober package and GRUB setting are verified. systemd-boot and Limine do
  not detect Windows on a different EFI partition (the UI recommends GRUB).
- **Post-install chroot shell** offer (CLI/TUI) is covered by unit tests only.
- **VMware, VirtualBox, Hyper-V** guest tools and **real Wi-Fi hardware** are
  covered by unit tests and `mac80211_hwsim` only.
- **Login screen keyboard layout** with Plasma Login Manager is untested;
  only the X11 and Plasma user (`kxkbrc`) layouts are written.
- **Wi-Fi passphrase** is briefly visible in the live session's process list
  (`iwctl --passphrase`); never logged.
- **TUI keyboard list** (98 layouts) has no type-to-jump search.
- **pacstrap warns** "directory permissions differ on /boot" because the ESP is
  mounted with `umask=0077`; harmless.
- **Manual partitioning** beyond "existing partitions" (custom mount points,
  separate `/home`, LVM, RAID) is not offered.
