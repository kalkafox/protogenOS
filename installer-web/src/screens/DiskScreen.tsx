import { useEffect, useState } from "react"

import { getDiskLayout, getDisks } from "@/api/client"
import type { DiskChoice, DiskInfo, DiskLayout, DiskLayoutKind, Firmware } from "@/api/types"
import { ChoiceList } from "@/components/ChoiceList"
import { PartitionBar } from "@/components/PartitionBar"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { NativeSelect } from "@/components/ui/select"
import { LoadingText } from "@/components/ui/spinner"
import { formatSize } from "@/lib/format"

const GIB = 1024 ** 3
const MIN_ROOT = 16 * GIB
const FREE_SPACE_NEEDED = MIN_ROOT + GIB

function describePartition(part: DiskLayout["partitions"][number]): string {
  const details = [part.fstype || "unformatted", part.label].filter(Boolean).join(", ")
  return `${part.path} — ${formatSize(part.size)} (${details})`
}

export function DiskScreen({
  firmware,
  value,
  onBack,
  onNext,
}: {
  firmware: Firmware
  value: DiskChoice | null
  onBack: () => void
  onNext: (choice: DiskChoice) => void
}) {
  const [disks, setDisks] = useState<DiskInfo[] | null>(null)
  const [disk, setDisk] = useState(value?.disk ?? "")
  const [layout, setLayout] = useState<DiskLayout | null>(null)
  const [kind, setKind] = useState<DiskLayoutKind>(value?.disk_layout ?? "erase")
  const [rootPartition, setRootPartition] = useState(value?.root_partition ?? "")
  const [bootPartition, setBootPartition] = useState(value?.boot_partition ?? "")
  const [formatBoot, setFormatBoot] = useState(value?.format_boot ?? false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getDisks()
      .then((data) => setDisks(data.disks))
      .catch((err) => {
        setDisks([])
        setError(String(err))
      })
  }, [])

  useEffect(() => {
    if (!disk) return
    setLayout(null)
    getDiskLayout(disk)
      .then(setLayout)
      .catch((err) => setError(String(err)))
  }, [disk])

  const uefi = firmware === "uefi"
  const freeSpaceOk = uefi && layout?.table === "gpt" && (layout?.largest_free ?? 0) >= FREE_SPACE_NEEDED
  const partitions = layout?.partitions ?? []
  const rootCandidates = partitions.filter((part) => part.size >= MIN_ROOT && part.mountpoints.length === 0)
  const bootCandidates = partitions.filter((part) => part.path !== rootPartition && part.mountpoints.length === 0)
  const bootInfo = partitions.find((part) => part.path === bootPartition)
  const partitionsOk =
    uefi &&
    rootPartition !== "" &&
    bootPartition !== "" &&
    rootPartition !== bootPartition &&
    (formatBoot || bootInfo?.fstype === "vfat")
  const selectedDisk = disks?.find((item) => item.path === disk)
  const ready =
    selectedDisk !== undefined &&
    layout !== null &&
    (kind === "erase" || (kind === "free-space" && freeSpaceOk) || (kind === "partitions" && partitionsOk))

  const handleNext = () => {
    if (!selectedDisk) return
    onNext({
      disk,
      disk_layout: kind,
      root_partition: kind === "partitions" ? rootPartition : null,
      boot_partition: kind === "partitions" ? bootPartition : null,
      format_boot: kind === "partitions" && formatBoot,
      disk_partitioned: selectedDisk.partitioned,
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Where should protogenOS go?</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {error && <p className="text-destructive text-sm">{error}</p>}
        {disks === null && <LoadingText>Looking for disks…</LoadingText>}
        {disks?.length === 0 && !error && (
          <p className="text-muted-foreground text-sm">No eligible disks found.</p>
        )}
        <ChoiceList
          name="disk"
          value={disk}
          onChange={(next) => {
            setDisk(next)
            setRootPartition("")
            setBootPartition("")
          }}
          choices={(disks ?? []).map((item) => ({
            value: item.path,
            title: `${item.path} — ${item.model}, ${formatSize(item.size)}`,
            description: item.partitioned
              ? "Has existing partitions."
              : item.removable
                ? "Removable disk."
                : "Empty disk.",
          }))}
        />

        {disk && layout === null && !error && <LoadingText>Reading partitions…</LoadingText>}

        {layout && (
          <div className="flex flex-col gap-2">
            <PartitionBar
              layout={layout}
              kind={kind}
              rootPartition={rootPartition}
              bootPartition={bootPartition}
              formatBoot={formatBoot}
            />
            <Label className="pt-2">Installation type</Label>
            <ChoiceList
              name="layout"
              value={kind}
              onChange={(next) => setKind(next as DiskLayoutKind)}
              choices={[
                {
                  value: "erase",
                  title: "Erase the entire disk",
                  description: "Deletes every partition and all data on this disk.",
                },
                {
                  value: "free-space",
                  title: "Install alongside other systems",
                  description: !uefi
                    ? "Requires UEFI boot."
                    : layout.table !== "gpt"
                      ? "Requires a GPT partition table."
                      : `Uses the largest unallocated space (${formatSize(layout.largest_free)} available, needs ${formatSize(FREE_SPACE_NEEDED)}). Existing partitions are kept.`,
                  disabled: !freeSpaceOk,
                },
                {
                  value: "partitions",
                  title: "Use existing partitions",
                  description: uefi
                    ? "Pick a root partition to format and an EFI system partition for /boot."
                    : "Requires UEFI boot.",
                  disabled: !uefi || partitions.length < 2,
                },
              ]}
            />
          </div>
        )}

        {layout && kind === "partitions" && (
          <div className="flex flex-col gap-3 rounded-md border p-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="root-partition">Root partition (will be formatted)</Label>
              <NativeSelect
                id="root-partition"
                value={rootPartition}
                onChange={(event) => setRootPartition(event.target.value)}
              >
                <option value="">Choose…</option>
                {rootCandidates.map((part) => (
                  <option key={part.path} value={part.path}>
                    {describePartition(part)}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="boot-partition">EFI system partition (mounted at /boot)</Label>
              <NativeSelect
                id="boot-partition"
                value={bootPartition}
                onChange={(event) => setBootPartition(event.target.value)}
              >
                <option value="">Choose…</option>
                {bootCandidates.map((part) => (
                  <option key={part.path} value={part.path}>
                    {describePartition(part)}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="format-boot"
                checked={formatBoot}
                onCheckedChange={(checked) => setFormatBoot(checked === true)}
              />
              <Label htmlFor="format-boot" className="font-normal">
                Format the EFI partition (removes other systems' bootloaders on it)
              </Label>
            </div>
            {bootInfo && !formatBoot && bootInfo.fstype !== "vfat" && (
              <p className="text-destructive text-xs">
                This partition isn't FAT32; format it or pick the existing EFI partition.
              </p>
            )}
            {bootInfo && bootInfo.size < 512 * 1024 ** 2 && (
              <p className="text-destructive text-xs">
                Kernels are stored on this partition; at least 512 MiB is recommended.
              </p>
            )}
          </div>
        )}

        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button disabled={!ready} onClick={handleNext}>
            Next
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
