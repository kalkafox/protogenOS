export type Persona = "general" | "gamer" | "developer" | "server" | "minimal"

export interface PackageChoice {
  group: string
  selection: "one-of" | "any-of" | "optional"
  identifier: string
  label: string
  package: string
  source: "official" | "aur" | "future"
  default: boolean
  profiles: string[]
  default_profiles: string[]
}

export interface OptionGroup {
  name: string
  selection: "one-of" | "any-of" | "optional"
  choices: PackageChoice[]
}

export interface InstallPlan {
  persona: string
  packages: string[]
  selections: Record<string, string[]>
  aur_packages: string[]
  multilib_required: boolean
}

export interface HardwareSummary {
  microcode: string | null
  gpus: string[]
  hypervisor: string | null
  packages: string[]
  // null when there is no NVIDIA GPU; true for Turing and newer.
  nvidia_open_supported: boolean | null
}

export interface FeatureSupport {
  tpm2: boolean
  fingerprint_reader: boolean
  secure_boot_setup_mode: boolean | null
  secure_boot_enabled: boolean | null
}

export interface SystemInfo {
  firmware: Firmware
  dry_run: boolean
  hardware: HardwareSummary
  features: FeatureSupport
}

export interface NetworkStatus {
  online: boolean
  devices: string[]
}

export interface WifiNetwork {
  ssid: string
  security: "open" | "psk" | "8021x" | "wep"
  signal: number
  connected: boolean
  known: boolean
}

export interface DiskInfo {
  path: string
  size: number
  model: string
  removable: boolean
  partitioned: boolean
}

export type Firmware = "uefi" | "bios"
export type DiskLayoutKind = "erase" | "free-space" | "partitions"
export type Filesystem = "btrfs" | "ext4" | "xfs" | "f2fs"
export type Bootloader = "grub" | "systemd-boot" | "limine"

export interface PartitionInfo {
  number: number
  path: string
  start: number
  end: number
  size: number
  fstype: string
  label: string
  type_name: string
  mountpoints: string[]
}

export interface DiskLayout {
  path: string
  size: number
  table: string | null
  partitions: PartitionInfo[]
  largest_free: number
}

export interface KeyboardVariant {
  code: string
  description: string
}

export interface KeyboardLayout {
  code: string
  description: string
  variants: KeyboardVariant[]
}

export interface KeyboardState {
  layout: string
  variant: string
  applied: boolean
  restart?: boolean
}

export interface MirrorCountry {
  name: string
  code: string
  count: number
}

export interface UserAccount {
  username: string
  password: string
  sudo: boolean
}

export interface DiskChoice {
  disk: string
  disk_layout: DiskLayoutKind
  root_partition: string | null
  boot_partition: string | null
  format_boot: boolean
  disk_partitioned: boolean
  // free-space only: shrink this partition to shrink_size bytes first.
  shrink_partition: string | null
  shrink_size: number
}

export interface StorageChoice {
  filesystem: Filesystem
  btrfs_subvolumes: boolean
  encrypt: boolean
  encryption_passphrase: string | null
  swap: "zram" | "none"
  bootloader: Bootloader
}

export interface SystemChoice {
  hostname: string
  username: string
  user_password: string
  timezone: string
  locale: string
  grant_sudo: boolean
  root_password: string | null
  mirror_country: string
  kernel_headers: boolean
  additional_users: UserAccount[]
}

export type NvidiaDriver = "nouveau" | "nvidia-open"

export interface FeatureChoice {
  snapshots: boolean
  flatpak: boolean
  gaming_tweaks: boolean
  nvidia_driver: NvidiaDriver
  fingerprint: boolean
  tpm2_unlock: boolean
  secure_boot: boolean
  // Server persona extras.
  cockpit: boolean
  netdata: boolean
  fail2ban: boolean
  update_downloads: boolean
  serial_console: boolean
}

// Server persona only; empty for every other persona.
export interface ServerChoice {
  ssh_authorized_keys: string[]
  static_address: string
  static_gateway: string
  static_dns: string[]
  static_interface: string
}

export interface InstallConfig extends DiskChoice, StorageChoice, FeatureChoice, SystemChoice, ServerChoice {
  firmware: Firmware
  keyboard_layout: string
  keyboard_variant: string
}

export interface InstallStatus {
  status: "idle" | "running" | "done" | "error"
  lines: string[]
  next_since: number
  error: string | null
  step: { index: number; total: number; title: string } | null
  warnings: string[]
}

export interface ApiErrorBody {
  error: string
}

export type PrefetchState = "idle" | "preparing" | "downloading" | "done" | "skipped" | "failed" | "stopped"

export interface PrefetchStatus {
  state: PrefetchState
  packages?: number
  total_bytes?: number
  // The part of total_bytes that fits in free memory.
  planned_bytes?: number
  downloaded_bytes?: number
  reason?: string
}

export interface PreflightCheck {
  id: string
  status: "ok" | "warning" | "error"
  title: string
  detail: string
}

export interface ShrinkInfo {
  partition: string
  fstype: string
  size: number
  smallest_size: number
  largest_room: number
  shrinkable: boolean
  reason: string
}
