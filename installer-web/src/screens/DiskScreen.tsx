import { useEffect, useState } from "react"

import { getDiskLayout, getDisks, getShrinkInfo } from "@/api/client"
import type { DiskChoice, DiskInfo, DiskLayout, DiskLayoutKind, Firmware, ShrinkInfo } from "@/api/types"
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
const MIB = 1024 ** 2
const SHRINKABLE = ["ntfs", "ext4"]

// The screen's choices; "shrink" is installing alongside after shrinking.
type Mode = DiskLayoutKind | "shrink"

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
  const [mode, setMode] = useState<Mode>(value?.shrink_partition ? "shrink" : (value?.disk_layout ?? "erase"))
  const kind: DiskLayoutKind = mode === "shrink" ? "free-space" : mode
  const [shrinkPartition, setShrinkPartition] = useState(value?.shrink_partition ?? "")
  const [shrinkInfo, setShrinkInfo] = useState<ShrinkInfo | null>(null)
  const [shrinkLoading, setShrinkLoading] = useState(false)
  // Bytes protogenOS receives from the shrunk partition.
  const [room, setRoom] = useState(0)
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

  useEffect(() => {
    setShrinkInfo(null)
    if (!disk || !shrinkPartition) return
    setShrinkLoading(true)
    getShrinkInfo(disk, shrinkPartition)
      .then((info) => {
        setShrinkInfo(info)
        const previous = value?.shrink_partition === shrinkPartition ? info.size - (value?.shrink_size ?? 0) : 0
        const half = Math.floor(info.largest_room / 2 / GIB) * GIB
        const preferred = previous || Math.max(FREE_SPACE_NEEDED, half)
        setRoom(Math.min(Math.max(preferred, FREE_SPACE_NEEDED), info.largest_room))
      })
      .catch((err) => setError(String(err)))
      .finally(() => setShrinkLoading(false))
  }, [disk, shrinkPartition, value])

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
  const shrinkCandidates = partitions.filter(
    (part) => SHRINKABLE.includes(part.fstype) && part.mountpoints.length === 0 && part.size > FREE_SPACE_NEEDED + 2 * GIB
  )
  const shrinkAvailable = uefi && layout?.table === "gpt" && shrinkCandidates.length > 0
  const shrinkOk = shrinkAvailable && shrinkInfo?.shrinkable === true && room >= FREE_SPACE_NEEDED
  // Whole MiB, so the freed space starts on an aligned boundary.
  const shrinkSize = shrinkInfo ? Math.floor((shrinkInfo.size - room) / MIB) * MIB : 0
  const shrinkLabel = (() => {
    const part = partitions.find((item) => item.path === shrinkPartition)
    return part?.label || (part?.fstype === "ntfs" ? "Windows" : shrinkPartition)
  })()
  const selectedDisk = disks?.find((item) => item.path === disk)
  const ready =
    selectedDisk !== undefined &&
    layout !== null &&
    (mode === "erase" ||
      (mode === "free-space" && freeSpaceOk) ||
      (mode === "shrink" && shrinkOk) ||
      (mode === "partitions" && partitionsOk))

  const handleNext = () => {
    if (!selectedDisk) return
    onNext({
      disk,
      disk_layout: kind,
      root_partition: kind === "partitions" ? rootPartition : null,
      boot_partition: kind === "partitions" ? bootPartition : null,
      format_boot: kind === "partitions" && formatBoot,
      disk_partitioned: selectedDisk.partitioned,
      shrink_partition: mode === "shrink" ? shrinkPartition : null,
      shrink_size: mode === "shrink" ? shrinkSize : 0,
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
            setShrinkPartition("")
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
              shrink={mode === "shrink" && shrinkOk ? { partition: shrinkPartition, newSize: shrinkSize } : null}
            />
            <Label className="pt-2">Installation type</Label>
            <ChoiceList
              name="layout"
              value={mode}
              onChange={(next) => setMode(next as Mode)}
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
                  value: "shrink",
                  title: "Make room by shrinking a partition",
                  description: !uefi
                    ? "Requires UEFI boot."
                    : layout.table !== "gpt"
                      ? "Requires a GPT partition table."
                      : shrinkCandidates.length === 0
                        ? "No NTFS (Windows) or ext4 partition with enough room."
                        : "Shrinks a Windows or Linux partition and installs into the freed space. Its files are kept.",
                  disabled: !shrinkAvailable,
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

        {layout && mode === "shrink" && (
          <div className="flex flex-col gap-3 rounded-md border p-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="shrink-partition">Partition to shrink</Label>
              <NativeSelect
                id="shrink-partition"
                value={shrinkPartition}
                onChange={(event) => setShrinkPartition(event.target.value)}
              >
                <option value="">Choose…</option>
                {shrinkCandidates.map((part) => (
                  <option key={part.path} value={part.path}>
                    {describePartition(part)}
                  </option>
                ))}
              </NativeSelect>
            </div>
            {shrinkLoading && <LoadingText>Measuring how much space is in use…</LoadingText>}
            {shrinkInfo && !shrinkInfo.shrinkable && (
              <p className="text-destructive text-xs">{shrinkInfo.reason}</p>
            )}
            {shrinkInfo?.shrinkable && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="shrink-room">Space for protogenOS</Label>
                <input
                  id="shrink-room"
                  type="range"
                  className="accent-primary w-full"
                  min={FREE_SPACE_NEEDED}
                  max={shrinkInfo.largest_room}
                  step={GIB}
                  value={room}
                  onChange={(event) => setRoom(Number(event.target.value))}
                />
                <div className="text-muted-foreground flex justify-between text-xs">
                  <span>
                    {shrinkLabel} keeps {formatSize(shrinkSize)}
                  </span>
                  <span className="text-foreground">protogenOS gets {formatSize(room)}</span>
                </div>
                <p className="text-muted-foreground text-xs">
                  Back up important files first.
                  {shrinkInfo.fstype === "ntfs" &&
                    " Windows must have been shut down fully: turn off Fast Startup and don't hibernate."}
                </p>
              </div>
            )}
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
