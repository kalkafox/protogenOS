export type Persona = "general" | "gamer" | "developer" | "minimal"

export interface PackageChoice {
  group: string
  selection: "one-of" | "any-of" | "optional"
  identifier: string
  label: string
  package: string
  source: "official" | "aur" | "future"
  default: boolean
  profiles: string[]
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
}

export interface SystemInfo {
  firmware: Firmware
  dry_run: boolean
  hardware: HardwareSummary
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
}

export interface StorageChoice {
  filesystem: Filesystem
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

export interface InstallConfig extends DiskChoice, StorageChoice, SystemChoice {
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
