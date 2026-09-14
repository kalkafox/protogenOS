import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"

export function AurConfirmScreen({
  packages,
  onBack,
  onNext,
}: {
  packages: string[]
  onBack: () => void
  onNext: () => void
}) {
  const [confirmed, setConfirmed] = useState(false)

  return (
    <Card>
      <CardHeader>
        <CardTitle>AUR packages required</CardTitle>
        <CardDescription>
          The following packages come from the Arch User Repository and will be built from
          source on your machine, not from Arch's signed official repositories.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ul className="list-inside list-disc text-sm">
          {packages.map((pkg) => (
            <li key={pkg}>{pkg}</li>
          ))}
        </ul>
        <div className="flex items-center gap-2">
          <Checkbox
            id="aur-confirm"
            checked={confirmed}
            onCheckedChange={(checked) => setConfirmed(checked === true)}
          />
          <Label htmlFor="aur-confirm" className="font-normal">
            I understand these packages will be built from AUR source
          </Label>
        </div>
        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button disabled={!confirmed} onClick={onNext}>
            Next
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
