import { useEffect, useRef, useState } from "react"

import { getInstallStatus } from "@/api/client"
import type { InstallStatus } from "@/api/types"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"

export function ProgressScreen({
  onDone,
  onError,
}: {
  onDone: (warnings: string[]) => void
  onError: (message: string) => void
}) {
  const [lines, setLines] = useState<string[]>([])
  const [step, setStep] = useState<InstallStatus["step"]>(null)
  const sinceRef = useRef(0)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      if (cancelled) return
      try {
        const status = await getInstallStatus(sinceRef.current)
        sinceRef.current = status.next_since
        if (status.lines.length > 0) {
          setLines((current) => [...current, ...status.lines])
        }
        setStep(status.step)
        if (status.status === "done") {
          onDone(status.warnings)
          return
        }
        if (status.status === "error") {
          onError(status.error ?? "installation failed")
          return
        }
      } catch {
        // transient — keep polling
      }
      if (!cancelled) setTimeout(poll, 750)
    }
    poll()
    return () => {
      cancelled = true
    }
  }, [onDone, onError])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" })
  }, [lines])

  // A step counts as done once the next one starts; give half credit for
  // the step in progress so the bar moves as soon as installation begins.
  const progressValue = step
    ? Math.min(100, Math.round(((step.index - 0.5) / step.total) * 100))
    : 0

  return (
    <Card>
      <CardHeader>
        <CardTitle>Installing protogenOS</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <p className="text-sm font-medium">
            {step ? `Step ${step.index} of ${step.total}: ${step.title}` : "Starting…"}
          </p>
          <Progress value={progressValue} />
        </div>
        <ScrollArea className="bg-muted h-80 rounded-md border p-3">
          <pre className="font-mono text-xs whitespace-pre-wrap">
            {lines.join("\n")}
            <div ref={bottomRef} />
          </pre>
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
