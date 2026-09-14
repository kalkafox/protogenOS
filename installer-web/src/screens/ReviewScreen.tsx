import { useEffect, useState } from "react"
import { TriangleAlert } from "lucide-react"

import { getDiskLayout, getDisks, getSystem } from "@/api/client"
import type { DiskInfo, DiskLayout, HardwareSummary, InstallConfig, InstallPlan } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"
import { formatSize } from "@/lib/format"

const BOOTLOADER_NAMES = { grub: "GRUB", "systemd-boot": "systemd-boot", limine: "Limine" }

interface DataWarning {
  title: string
  // Existing partitions that will be destroyed; empty when nothing is lost.
  lost: DiskLayout["partitions"]
  acknowledgement: string
  action: string
}

function diskName(disk: DiskInfo | undefined, path: string): string {
  return disk ? `${disk.model || "Disk"} (${path}, ${formatSize(disk.size)})` : path
}

// Spell out, in plain language, exactly what the chosen layout destroys.
function dataWarning(config: InstallConfig, disk: DiskInfo | undefined, layout: DiskLayout | null): DataWarning {
  const partitions = layout?.partitions ?? []
  if (config.disk_layout === "partitions") {
    const lost = partitions.filter(
      (part) =>
        part.path === config.root_partition || (config.format_boot && part.path === config.boot_partition)
    )
    return {
      title: `The selected partitions on ${diskName(disk, config.disk)} will be formatted`,
      lost,
      acknowledgement: "I understand the files on these partitions will be permanently deleted.",
      action: "Format and install",
    }
  }
  if (config.disk_layout === "free-space") {
    return {
      title: `protogenOS will be installed in the free space on ${diskName(disk, config.disk)}`,
      lost: [],
      acknowledgement: "I understand new partitions will be created on this disk.",
      action: "Install protogenOS",
    }
  }
  return {
    title: `Everything on ${diskName(disk, config.disk)} will be erased`,
    lost: partitions,
    acknowledgement: "I have backed up anything I want to keep from this disk.",
    action: "Erase disk and install",
  }
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
  onInstall: () => Promise<void>
}) {
  const [acknowledged, setAcknowledged] = useState(false)
  const [starting, setStarting] = useState(false)
  const [hardware, setHardware] = useState<HardwareSummary | null>(null)
  const [disk, setDisk] = useState<DiskInfo | undefined>(undefined)
  const [layout, setLayout] = useState<DiskLayout | null>(null)
  const warning = dataWarning(config, disk, layout)

  useEffect(() => {
    getSystem()
      .then((system) => setHardware(system.hardware))
      .catch(() => setHardware(null))
  }, [])

  // Disk details only enrich the warning; it still names the device without them.
  useEffect(() => {
    getDisks()
      .then((data) => setDisk(data.disks.find((item) => item.path === config.disk)))
      .catch(() => setDisk(undefined))
    getDiskLayout(config.disk)
      .then(setLayout)
      .catch(() => setLayout(null))
  }, [config.disk])

  const handleInstall = async () => {
    setStarting(true)
    try {
      await onInstall()
    } finally {
      setStarting(false)
    }
  }

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
        <div className="border-destructive/60 bg-destructive/10 flex flex-col gap-3 rounded-md border p-3">
          <div className="flex items-start gap-2">
            <TriangleAlert className="text-destructive mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <p className="text-sm font-medium">{warning.title}</p>
          </div>
          {warning.lost.length > 0 && (
            <ul className="text-muted-foreground flex flex-col gap-0.5 pl-6 text-xs">
              {warning.lost.map((part) => (
                <li key={part.path}>
                  <span className="font-mono">{part.path}</span> · {formatSize(part.size)}
                  {part.label && ` · “${part.label}”`}
                  {` · ${part.fstype || "unformatted"}`}
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-start gap-2">
            <Checkbox
              id="data-acknowledgement"
              className="mt-0.5"
              checked={acknowledged}
              onCheckedChange={(checked) => setAcknowledged(checked === true)}
            />
            <Label htmlFor="data-acknowledgement" className="leading-snug font-normal">
              {warning.acknowledgement}
            </Label>
          </div>
        </div>
        <div className="flex justify-between">
          <Button variant="outline" disabled={starting} onClick={onBack}>
            Back
          </Button>
          <Button
            variant={config.disk_layout === "free-space" ? "default" : "destructive"}
            aria-busy={starting}
            disabled={!acknowledged || starting}
            onClick={handleInstall}
          >
            {starting && <Spinner />}
            {starting ? "Starting…" : warning.action}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
