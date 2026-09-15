import { useEffect, useState } from "react"

import { getPersonas } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Label } from "@/components/ui/label"
import { LoadingText } from "@/components/ui/spinner"

const DESCRIPTIONS: Record<string, string> = {
  general: "Everyday desktop use with a browser and essentials.",
  gamer: "Gaming-focused, includes Steam/Lutris and multilib support.",
  developer: "Development tools, editors, and dotfiles.",
  server: "Headless server managed over SSH, with a firewall and optional services.",
  minimal: "Console-only system with the fewest packages; no desktop.",
}

export function PersonaScreen({
  value,
  onNext,
}: {
  value: string
  onNext: (persona: string) => void
}) {
  const [personas, setPersonas] = useState<string[]>([])
  const [selected, setSelected] = useState(value)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getPersonas()
      .then((data) => {
        setPersonas(data.personas)
        setSelected((current) => current || data.personas[0])
      })
      .catch((err) => setError(String(err)))
  }, [])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose a persona</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        {error && <p className="text-destructive text-sm">{error}</p>}
        {personas.length === 0 && !error && <LoadingText>Loading personas…</LoadingText>}
        <RadioGroup value={selected} onValueChange={setSelected}>
          {personas.map((persona) => (
            <div key={persona} className="flex items-start gap-3 rounded-md border p-3">
              <RadioGroupItem value={persona} id={persona} className="mt-1" />
              <Label htmlFor={persona} className="flex flex-col items-start gap-1 font-normal">
                <span className="font-medium capitalize">{persona}</span>
                <span className="text-muted-foreground text-sm">
                  {DESCRIPTIONS[persona] ?? ""}
                </span>
              </Label>
            </div>
          ))}
        </RadioGroup>
        <div className="flex justify-end">
          <Button disabled={!selected} onClick={() => onNext(selected)}>
            Next
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
