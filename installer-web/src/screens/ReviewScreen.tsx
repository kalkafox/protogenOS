import { useEffect, useState } from "react"

import { getSystem } from "@/api/client"
import type { HardwareSummary, InstallConfig, InstallPlan } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

const BOOTLOADER_NAMES = { grub: "GRUB", "systemd-boot": "systemd-boot", limine: "Limine" }

// The phrase names exactly what gets destroyed for each layout.
function confirmationPhrase(config: InstallConfig): string {
  if (config.disk_layout === "partitions") return `FORMAT ${config.root_partition}`
  if (config.disk_layout === "free-space") return `INSTALL ${config.disk}`
  return `ERASE ${config.disk}`
}

function describeTarget(config: InstallConfig): string {
  if (config.disk_layout === "partitions") {
    const boot = config.format_boot ? "formatted" : "kept"
    return `${config.root_partition} formatted as root; ${config.boot_partition} (${boot}) as /boot`
  }
  if (config.disk_layout === "free-space") {
    return `New partitions in the free space on ${config.disk}; existing partitions kept`
  }
  return `${config.disk} — entire disk will be erased`
}

export function ReviewScreen({
  plan,
  config,
  onBack,
  onInstall,
}: {
  plan: InstallPlan
  config: InstallConfig
  onBack: () => void
  onInstall: () => void
}) {
  const [confirmation, setConfirmation] = useState("")
  const [hardware, setHardware] = useState<HardwareSummary | null>(null)
  const expected = confirmationPhrase(config)

  useEffect(() => {
    getSystem()
      .then((system) => setHardware(system.hardware))
      .catch(() => setHardware(null))
  }, [])

  const rows: [string, string][] = [
    ["Persona", plan.persona],
    ["Target", describeTarget(config)],
    ["Filesystem", config.filesystem === "btrfs" ? "Btrfs with subvolumes, zstd compression" : config.filesystem],
    ["Encryption", config.encrypt ? "LUKS2 (passphrase at boot)" : "None"],
    ["Swap", config.swap === "zram" ? "zram (compressed RAM)" : "None"],
    ["Boot", `${BOOTLOADER_NAMES[config.bootloader]} · ${config.firmware.toUpperCase()}`],
    ["Keyboard", config.keyboard_variant ? `${config.keyboard_layout} (${config.keyboard_variant})` : config.keyboard_layout],
    ["Hostname", config.hostname],
    [
      "Users",
      [
        `${config.username}${config.grant_sudo ? " (sudo)" : ""}`,
        ...config.additional_users.map((user) => `${user.username}${user.sudo ? " (sudo)" : ""}`),
      ].join(", "),
    ],
    ["Timezone", config.timezone],
    ["Locale", config.locale],
    ["Mirrors", config.mirror_country || "Automatic"],
    ["Kernel headers", config.kernel_headers ? "Yes" : "No"],
  ]
  if (hardware) {
    rows.push([
      "Hardware",
      [
        hardware.microcode,
        hardware.gpus.length > 0 ? `${hardware.gpus.join(", ")} graphics` : null,
        hardware.hypervisor ? `${hardware.hypervisor} guest tools` : null,
      ]
        .filter(Boolean)
        .join(" · ") || "generic drivers",
    ])
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Review</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          {rows.map(([term, detail]) => (
            <div key={term} className="contents">
              <dt className="text-muted-foreground">{term}</dt>
              <dd className={term === "Target" ? "text-destructive" : undefined}>{detail}</dd>
            </div>
          ))}
        </dl>
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium">Packages ({plan.packages.length})</p>
          <p className="text-muted-foreground text-xs break-words">{plan.packages.join(", ")}</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="erase-confirmation">
            Type <span className="font-mono font-semibold">{expected}</span> to confirm
          </Label>
          <Input
            id="erase-confirmation"
            autoComplete="off"
            spellCheck={false}
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </div>
        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button
            variant="destructive"
            disabled={confirmation.trim() !== expected}
            onClick={onInstall}
          >
            Install protogenOS
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
