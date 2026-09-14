import { useEffect, useState } from "react"

import { getOptions } from "@/api/client"
import type { OptionGroup } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"

export function OptionsScreen({
  persona,
  value,
  onBack,
  onNext,
}: {
  persona: string
  value: Record<string, string[]>
  onBack: () => void
  onNext: (selections: Record<string, string[]>) => void
}) {
  const [groups, setGroups] = useState<OptionGroup[]>([])
  const [selections, setSelections] = useState<Record<string, string[]>>(value)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getOptions(persona)
      .then((data) => {
        setGroups(data.groups)
        setSelections((current) => {
          const next = { ...current }
          for (const group of data.groups) {
            if (!next[group.name]) {
              next[group.name] = group.choices
                .filter((choice) => choice.default)
                .map((choice) => choice.identifier)
            }
          }
          return next
        })
      })
      .catch((err) => setError(String(err)))
  }, [persona])

  const setOneOf = (group: string, identifier: string) =>
    setSelections((current) => ({ ...current, [group]: [identifier] }))

  const toggleAnyOf = (group: string, identifier: string, checked: boolean) =>
    setSelections((current) => {
      const existing = current[group] ?? []
      const next = checked
        ? [...existing, identifier]
        : existing.filter((item) => item !== identifier)
      return { ...current, [group]: next }
    })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose applications</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        {error && <p className="text-destructive text-sm">{error}</p>}
        {groups.map((group) => (
          <div key={group.name} className="flex flex-col gap-2">
            <p className="text-sm font-medium capitalize">{group.name.replace(/-/g, " ")}</p>
            {group.selection === "any-of" ? (
              <div className="flex flex-col gap-2">
                {group.choices.map((choice) => (
                  <div key={choice.identifier} className="flex items-center gap-2">
                    <Checkbox
                      id={`${group.name}-${choice.identifier}`}
                      checked={(selections[group.name] ?? []).includes(choice.identifier)}
                      onCheckedChange={(checked) =>
                        toggleAnyOf(group.name, choice.identifier, checked === true)
                      }
                    />
                    <Label htmlFor={`${group.name}-${choice.identifier}`} className="font-normal">
                      {choice.label}
                      {choice.source === "aur" && (
                        <span className="text-muted-foreground ml-1 text-xs">(AUR)</span>
                      )}
                    </Label>
                  </div>
                ))}
              </div>
            ) : (
              <RadioGroup
                value={(selections[group.name] ?? [])[0] ?? ""}
                onValueChange={(id) => setOneOf(group.name, id)}
              >
                {group.selection === "optional" && (
                  <div className="flex items-center gap-2">
                    <RadioGroupItem value="" id={`${group.name}-none`} />
                    <Label htmlFor={`${group.name}-none`} className="font-normal">
                      None
                    </Label>
                  </div>
                )}
                {group.choices.map((choice) => (
                  <div key={choice.identifier} className="flex items-center gap-2">
                    <RadioGroupItem value={choice.identifier} id={`${group.name}-${choice.identifier}`} />
                    <Label htmlFor={`${group.name}-${choice.identifier}`} className="font-normal">
                      {choice.label}
                      {choice.source === "aur" && (
                        <span className="text-muted-foreground ml-1 text-xs">(AUR)</span>
                      )}
                    </Label>
                  </div>
                ))}
              </RadioGroup>
            )}
          </div>
        ))}
        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button onClick={() => onNext(selections)}>Next</Button>
        </div>
      </CardContent>
    </Card>
  )
}
