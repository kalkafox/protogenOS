import { CircleCheck, Info } from "lucide-react"

import type { PrefetchStatus } from "@/api/types"
import { Progress } from "@/components/ui/progress"
import { formatSize } from "@/lib/format"

// Bytes the background download aims for, and how far it got.
export function prefetchProgress(status: PrefetchStatus) {
  const total = status.total_bytes ?? 0
  const planned = status.planned_bytes || total
  const downloaded = Math.min(status.downloaded_bytes ?? 0, planned)
  const percent = planned > 0 ? Math.round((downloaded / planned) * 100) : 0
  return { total, planned, downloaded, percent, partial: planned < total }
}

// Slim status under the title while packages download in the background.
export function PrefetchBar({ status }: { status: PrefetchStatus | null }) {
  if (!status) return null
  const { total, planned, downloaded, percent, partial } = prefetchProgress(status)

  if (status.state === "done") {
    return (
      <p className="text-muted-foreground mb-4 flex items-center justify-center gap-1.5 text-xs">
        <CircleCheck className="size-3.5" aria-hidden="true" />
        {partial
          ? `${formatSize(planned)} of ${formatSize(total)} downloaded ahead; the rest downloads while installing`
          : `${status.packages ?? 0} packages downloaded ahead (${formatSize(total)})`}
      </p>
    )
  }
  if (status.state === "skipped" || status.state === "failed") {
    return (
      <p className="text-muted-foreground mb-4 flex items-center justify-center gap-1.5 text-xs">
        <Info className="size-3.5" aria-hidden="true" />
        Packages will download during installation
        {status.reason ? ` (${status.reason})` : ""}
      </p>
    )
  }
  if (status.state !== "preparing" && status.state !== "downloading") return null

  return (
    <div className="mb-4 flex flex-col gap-1.5" role="status" aria-live="polite">
      <div className="text-muted-foreground flex justify-between gap-3 text-xs">
        <span>
          {status.state === "preparing"
            ? "Finding the fastest mirrors…"
            : partial
              ? "Downloading what fits in memory"
              : "Downloading packages in the background"}
        </span>
        {status.state === "downloading" && (
          <span>
            {formatSize(downloaded)} of {formatSize(planned)} · {percent}%
          </span>
        )}
      </div>
      <Progress value={status.state === "downloading" ? percent : 0} aria-label="Background download" />
    </div>
  )
}
