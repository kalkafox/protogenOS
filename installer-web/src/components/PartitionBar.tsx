import type { DiskLayout, DiskLayoutKind } from "@/api/types"
import { formatSize } from "@/lib/format"

type Fate = "kept" | "lost" | "new" | "free"

interface Segment {
  key: string
  start: number
  size: number
  label: string
  fate: Fate
}

const FATE_CLASSES: Record<Fate, string> = {
  kept: "bg-foreground/20 text-foreground",
  lost: "bg-destructive text-destructive-foreground",
  new: "bg-primary text-primary-foreground",
  free: "bg-muted text-muted-foreground",
}

const FATE_LEGEND: Record<Fate, string> = {
  kept: "Kept",
  lost: "Erased",
  new: "protogenOS",
  free: "Unallocated",
}

// Gaps smaller than this are alignment slack, not usable free space.
const MIN_GAP = 16 * 1024 ** 2

function segmentsFor(
  layout: DiskLayout,
  kind: DiskLayoutKind,
  rootPartition: string,
  bootPartition: string,
  formatBoot: boolean
): Segment[] {
  if (kind === "erase") {
    return [{ key: "new", start: 0, size: layout.size, label: "protogenOS", fate: "new" }]
  }
  const segments: Segment[] = []
  let cursor = 0
  const sorted = [...layout.partitions].sort((a, b) => a.start - b.start)
  for (const part of sorted) {
    if (part.start - cursor >= MIN_GAP) {
      segments.push({ key: `gap-${cursor}`, start: cursor, size: part.start - cursor, label: "free", fate: "free" })
    }
    const replaced =
      kind === "partitions" && (part.path === rootPartition || (formatBoot && part.path === bootPartition))
    const name = part.label || part.fstype || part.path.replace(/^\/dev\//, "")
    segments.push({
      key: part.path,
      start: part.start,
      size: part.size,
      label: kind === "partitions" && part.path === rootPartition ? "protogenOS" : name,
      fate: kind === "partitions" && part.path === rootPartition ? "new" : replaced ? "lost" : "kept",
    })
    cursor = Math.max(cursor, part.end + 1)
  }
  if (layout.size - cursor >= MIN_GAP) {
    segments.push({ key: `gap-${cursor}`, start: cursor, size: layout.size - cursor, label: "free", fate: "free" })
  }
  if (kind === "free-space") {
    // The installer uses the largest unallocated region.
    const largest = segments
      .filter((segment) => segment.fate === "free")
      .reduce<Segment | null>((best, segment) => (best && best.size >= segment.size ? best : segment), null)
    if (largest) {
      largest.fate = "new"
      largest.label = "protogenOS"
    }
  }
  return segments
}

export function PartitionBar({
  layout,
  kind,
  rootPartition = "",
  bootPartition = "",
  formatBoot = false,
}: {
  layout: DiskLayout
  kind: DiskLayoutKind
  rootPartition?: string
  bootPartition?: string
  formatBoot?: boolean
}) {
  const segments = segmentsFor(layout, kind, rootPartition, bootPartition, formatBoot)
  const fates = [...new Set(segments.map((segment) => segment.fate))]
  return (
    <div className="flex flex-col gap-1.5">
      <div
        className="flex h-9 w-full overflow-hidden rounded-md border"
        role="img"
        aria-label={segments.map((segment) => `${segment.label}, ${formatSize(segment.size)}, ${FATE_LEGEND[segment.fate]}`).join("; ")}
      >
        {segments.map((segment) => (
          <div
            key={segment.key}
            title={`${segment.label} — ${formatSize(segment.size)}`}
            className={`flex min-w-1 items-center justify-center overflow-hidden border-r px-1 text-xs whitespace-nowrap last:border-r-0 ${FATE_CLASSES[segment.fate]}`}
            style={{ flexGrow: segment.size, flexBasis: 0 }}
          >
            {segment.size / layout.size > 0.12 && `${segment.label} · ${formatSize(segment.size)}`}
          </div>
        ))}
      </div>
      <div className="text-muted-foreground flex flex-wrap gap-3 text-xs">
        {fates.map((fate) => (
          <span key={fate} className="flex items-center gap-1">
            <span className={`inline-block size-2.5 rounded-sm ${FATE_CLASSES[fate]}`} />
            {FATE_LEGEND[fate]}
          </span>
        ))}
      </div>
    </div>
  )
}
