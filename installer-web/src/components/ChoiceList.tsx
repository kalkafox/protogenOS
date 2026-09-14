import type { ReactNode } from "react"

import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"

export interface Choice {
  value: string
  title: ReactNode
  description?: ReactNode
  disabled?: boolean
}

// Radio options rendered as bordered rows with an optional explanation.
export function ChoiceList({
  name,
  value,
  choices,
  onChange,
}: {
  name: string
  value: string
  choices: Choice[]
  onChange: (value: string) => void
}) {
  return (
    <RadioGroup value={value} onValueChange={onChange} className="flex flex-col gap-2">
      {choices.map((choice) => {
        const id = `${name}-${choice.value}`
        return (
          <div
            key={choice.value}
            className={`flex items-start gap-3 rounded-md border p-3 ${choice.disabled ? "opacity-50" : ""}`}
          >
            <RadioGroupItem value={choice.value} id={id} disabled={choice.disabled} className="mt-1" />
            <Label htmlFor={id} className="flex flex-col items-start gap-1 font-normal">
              <span className="font-medium">{choice.title}</span>
              {choice.description && (
                <span className="text-muted-foreground text-xs">{choice.description}</span>
              )}
            </Label>
          </div>
        )
      })}
    </RadioGroup>
  )
}
