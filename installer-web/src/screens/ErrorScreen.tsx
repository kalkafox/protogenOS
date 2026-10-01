import { useEffect, useState } from "react"

import { getInstallStatus, uploadInstallLog } from "@/api/client"
import { LogView } from "@/components/LogView"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"

type ShareState =
  | { kind: "idle" }
  | { kind: "confirm" }
  | { kind: "uploading" }
  | { kind: "shared"; url: string }
  | { kind: "failed"; message: string }

export function ErrorScreen({
  message,
  onRestart,
}: {
  message: string
  onRestart: () => void
}) {
  const [lines, setLines] = useState<string[]>([])
  const [share, setShare] = useState<ShareState>({ kind: "idle" })

  useEffect(() => {
    getInstallStatus(0)
      .then((status) => setLines(status.lines))
      .catch(() => setLines([]))
  }, [])

  const upload = async () => {
    setShare({ kind: "uploading" })
    try {
      const result = await uploadInstallLog()
      setShare({ kind: "shared", url: result.url })
    } catch (err) {
      setShare({ kind: "failed", message: String(err) })
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-destructive">Installation failed</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="font-mono text-sm break-words">{message}</p>

        {lines.length > 0 && (
          <>
            <LogView lines={lines} className="h-96" />

            {share.kind === "confirm" && (
              <div className="flex flex-col gap-3 rounded-md border p-3">
                <p className="text-sm">
                  Upload this log to <span className="font-mono">paste.rs</span>? Anyone with the link can
                  read it. It contains disk and partition names, the hostname, user names, and the
                  package list, but no passwords.
                </p>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={() => setShare({ kind: "idle" })}>
                    Cancel
                  </Button>
                  <Button onClick={upload}>Upload</Button>
                </div>
              </div>
            )}

            {share.kind === "shared" && (
              <div className="flex flex-col gap-1 rounded-md border p-3">
                <p className="text-muted-foreground text-xs">Include this link in your bug report:</p>
                <p className="font-mono text-lg break-all select-all">{share.url}</p>
              </div>
            )}

            {share.kind === "failed" && <p className="text-destructive text-sm">{share.message}</p>}
          </>
        )}

        <div className="flex justify-between gap-2">
          {lines.length > 0 && share.kind !== "shared" ? (
            <Button
              variant="outline"
              disabled={share.kind === "uploading" || share.kind === "confirm"}
              onClick={() => setShare({ kind: "confirm" })}
            >
              {share.kind === "uploading" && <Spinner />}
              {share.kind === "uploading" ? "Uploading…" : "Share log for a bug report"}
            </Button>
          ) : (
            <span />
          )}
          <Button onClick={onRestart}>Back to review</Button>
        </div>
      </CardContent>
    </Card>
  )
}
