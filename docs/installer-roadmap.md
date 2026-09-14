# Installer Roadmap

Where the installer stands and what is left. P0 (reliability) and P1
(archinstall parity) are done and verified in QEMU; everything below is open.

## Decisions needed

- **AUR themes on by default.** `sweet-theme-git` and `sddm-eucalyptus-drop`
  are `default=yes` in `profiles/options.conf`, so every default install needs
  AUR opt-in and `--non-interactive` fails without `--allow-aur`. This is the
  cause of the 3 failing tests in `tests/test_profiles.py`. Either make them
  opt-in or update the tests.
- **Duplicate login manager.** `plasma-meta` now pulls `plasma-login-manager`
  alongside SDDM. SDDM is the one enabled; decide whether to switch or exclude
  the other.

## Housekeeping

- Commit the work (currently all uncommitted), in logical pieces: GUI launcher
  fix, P0, P1.
- `docs/installer.md` predates the web GUI, network step, and P1 options;
  rewrite it (README already covers the user-facing summary).
- Add the Playwright GUI walkthrough (dry-run API, mocked disks) as a script
  and run it in CI next to the unit tests.
- Add a QEMU install smoke test to CI for at least one layout.

## P2 — beyond archinstall

- **Snapshots:** snapper with the existing `@snapshots` subvolume,
  snap-pac pre/post pacman snapshots, and grub-btrfs boot entries.
- **GPU drivers:** detect NVIDIA generation and offer `nvidia-open` (Turing
  and newer) with headers, or keep nouveau; hybrid-graphics handling.
- **Secure Boot:** sbctl key enrollment and signed boot files, for
  systemd-boot and Limine first.
- **TPM2 unlock** for LUKS (`systemd-cryptenroll`), keeping the passphrase as
  a fallback.
- **Fingerprint** enrollment when a reader is present.
- **Flatpak/Flathub** toggle.
- **Gaming tweaks:** `vm.max_map_count`, gamemode group membership.
- **Locale suggestion** from the chosen timezone or keyboard layout.
- **GUI polish:** disk layout preview (partition bar), live download progress,
  "copy log / upload for a bug report" on the error screen.
- **Boot menu entries** for `protogenos.installer=gui|tui`.

## Known gaps and unverified paths

- **Windows dual boot** never tested against a real Windows install; only the
  os-prober package and GRUB setting are verified. systemd-boot and Limine do
  not detect Windows on a different EFI partition (the UI recommends GRUB).
- **Post-install chroot shell** offer (CLI/TUI) is covered by unit tests only.
- **VMware, VirtualBox, Hyper-V** guest tools and **real Wi-Fi hardware** are
  covered by unit tests and `mac80211_hwsim` only.
- **SDDM layout indicator** shows "us" even though Xorg applies the chosen
  layout.
- **Wi-Fi passphrase** is briefly visible in the live session's process list
  (`iwctl --passphrase`); never logged.
- **TUI keyboard list** (98 layouts) has no type-to-jump search.
- **pacstrap warns** "directory permissions differ on /boot" because the ESP is
  mounted with `umask=0077`; harmless.
- **Manual partitioning** beyond "existing partitions" (custom mount points,
  separate `/home`, LVM, RAID) is not offered.
