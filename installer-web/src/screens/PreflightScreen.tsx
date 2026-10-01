import { useEffect, useState } from "react"
import { CircleCheck, CircleX, TriangleAlert } from "lucide-react"

import { getPreflight } from "@/api/client"
import type { PreflightCheck } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { LoadingText } from "@/components/ui/spinner"

const ICONS = {
  ok: <CircleCheck className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-label="OK" />,
  warning: <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-400" aria-label="Warning" />,
  error: <CircleX className="text-destructive mt-0.5 size-4 shrink-0" aria-label="Problem" />,
}

// Machine checks before any choices: shown only when something needs attention.
export function PreflightScreen({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const [checks, setChecks] = useState<PreflightCheck[] | null>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    getPreflight()
      .then((data) => {
        if (data.checks.every((check) => check.status === "ok")) {
          onNext()
          return
        }
        setChecks(data.checks)
        setLoading(false)
      })
      // The checks only advise; never block installing on them failing.
      .catch(() => onNext())
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [])

  if (loading || !checks) {
    return (
      <Card>
        <CardContent className="py-10">
          <LoadingText className="text-foreground justify-center">Checking this computer…</LoadingText>
        </CardContent>
      </Card>
    )
  }

  const blocked = checks.some((check) => check.status === "error")
  const order = { error: 0, warning: 1, ok: 2 }
  const sorted = [...checks].sort((a, b) => order[a.status] - order[b.status])

  return (
    <Card>
      <CardHeader>
        <CardTitle>{blocked ? "This computer can't be installed to yet" : "Before you start"}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ul className="flex flex-col gap-3">
          {sorted.map((check, index) => (
            <li key={`${check.id}-${index}`} className="flex items-start gap-2">
              {ICONS[check.status]}
              <div className="flex flex-col gap-0.5">
                <span className={check.status === "ok" ? "text-muted-foreground text-sm" : "text-sm font-medium"}>
                  {check.title}
                </span>
                {check.detail && check.status !== "ok" && (
                  <span className="text-muted-foreground text-xs">{check.detail}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          {blocked ? (
            <Button onClick={load}>Check again</Button>
          ) : (
            <Button onClick={onNext}>Continue anyway</Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
