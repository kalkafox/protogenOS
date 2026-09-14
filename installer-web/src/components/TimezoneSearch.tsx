import { useEffect, useState } from "react"

import { getTimezones } from "@/api/client"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

// Common abbreviations users type that don't appear verbatim in IANA zone
// names, mapped to a keyword that does (e.g. "AST" -> Atlantic/* zones).
const TIMEZONE_ABBREVIATION_HINTS: Record<string, string> = {
  ast: "atlantic",
  adt: "atlantic",
  est: "eastern",
  edt: "eastern",
  cst: "central",
  cdt: "central",
  mst: "mountain",
  mdt: "mountain",
  pst: "pacific",
  pdt: "pacific",
  akst: "alaska",
  hst: "hawaii",
  gmt: "utc",
  bst: "london",
  cet: "paris",
  eet: "athens",
  jst: "tokyo",
  aest: "sydney",
  acst: "adelaide",
  awst: "perth",
  ist: "kolkata",
}

function matchesTimezoneQuery(name: string, query: string): boolean {
  const trimmed = query.trim().toLowerCase()
  if (!trimmed) return true
  const haystack = name.replace(/_/g, " ").replace(/\//g, " ").toLowerCase()
  return trimmed
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => {
      const candidate = TIMEZONE_ABBREVIATION_HINTS[word] ?? word
      return haystack.includes(candidate) || haystack.includes(word)
    })
}

export function TimezoneSearch({ value, onChange }: { value: string; onChange: (zone: string) => void }) {
  const [timezones, setTimezones] = useState<string[]>([])
  const [query, setQuery] = useState(value)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    getTimezones()
      .then((res) => setTimezones(res.timezones))
      .catch(() => setTimezones([]))
  }, [])

  useEffect(() => {
    setQuery(value)
  }, [value])

  const matches = timezones.filter((zone) => matchesTimezoneQuery(zone, query)).slice(0, 50)

  return (
    <div className="relative flex flex-col gap-1.5">
      <Label htmlFor="timezone">Timezone</Label>
      <Input
        id="timezone"
        value={query}
        placeholder="Search e.g. AST, atlantic, tokyo"
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && (
        <div className="bg-popover absolute top-full z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-md border shadow-md">
          {matches.length === 0 ? (
            <p className="text-muted-foreground p-2 text-sm">No matches</p>
          ) : (
            matches.map((zone) => (
              <button
                key={zone}
                type="button"
                className="hover:bg-accent w-full px-2 py-1 text-left text-sm"
                onMouseDown={(e) => {
                  e.preventDefault()
                  onChange(zone)
                  setQuery(zone)
                  setOpen(false)
                }}
              >
                {zone}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
