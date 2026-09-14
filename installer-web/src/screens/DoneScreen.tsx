import { useState } from "react"

import { rebootSystem } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"

export function DoneScreen({ warnings }: { warnings: string[] }) {
  const [error, setError] = useState<string | null>(null)
  const [rebooting, setRebooting] = useState(false)

  const handleReboot = async () => {
    setError(null)
    setRebooting(true)
    try {
      await rebootSystem()
    } catch (err) {
      setError(String(err))
      setRebooting(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Installation complete</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="text-sm">
          protogenOS has been installed. Remove the installation media and reboot to start using
          your new system.
        </p>
        {warnings.length > 0 && (
          <div className="flex flex-col gap-1 rounded-md border border-amber-500/50 p-3">
            <p className="text-sm font-medium">Finished with warnings</p>
            {warnings.map((warning) => (
              <p key={warning} className="text-muted-foreground text-xs break-words">
                {warning}
              </p>
            ))}
          </div>
        )}
        {error && <p className="text-destructive text-sm">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => window.location.reload()}>
            Start another install
          </Button>
          <Button aria-busy={rebooting} disabled={rebooting} onClick={handleReboot}>
            {rebooting && <Spinner />}
            {rebooting ? "Rebooting…" : "Reboot now"}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
