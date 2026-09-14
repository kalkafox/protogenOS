import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"

import { getKeyboard, getKeyboardLayouts, setKeyboard } from "@/api/client"
import type { KeyboardLayout } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { NativeSelect } from "@/components/ui/select"
import { LoadingText } from "@/components/ui/spinner"

export function KeyboardScreen({
  onNext,
}: {
  onNext: (layout: string, variant: string) => void
}) {
  const [layouts, setLayouts] = useState<KeyboardLayout[]>([])
  const [layout, setLayout] = useState("us")
  const [variant, setVariant] = useState("")
  const [query, setQuery] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const selectedRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    Promise.all([getKeyboardLayouts(), getKeyboard()])
      .then(([catalog, current]) => {
        setLayouts(catalog.layouts)
        setLayout(current.layout)
        setVariant(current.variant)
      })
      .catch((err) => setError(String(err)))
  }, [])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return layouts.filter(
      (item) =>
        !needle ||
        item.code.toLowerCase().includes(needle) ||
        item.description.toLowerCase().includes(needle)
    )
  }, [layouts, query])
  const selected = layouts.find((item) => item.code === layout)

  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: "nearest" })
  }, [layouts, layout])

  // Arrow keys move through the filtered list while typing in the search box,
  // so the whole screen works without a pointer.
  const handleSearchKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault()
      if (selected) handleApply()
      return
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return
    event.preventDefault()
    if (filtered.length === 0) return
    const index = filtered.findIndex((item) => item.code === layout)
    const step = event.key === "ArrowDown" ? 1 : -1
    const next = filtered[(index + step + filtered.length) % filtered.length]
    setLayout(next.code)
    setVariant("")
  }

  const handleApply = async () => {
    setApplying(true)
    setError(null)
    try {
      const result = await setKeyboard(layout, variant)
      if (result.restart) {
        // The kiosk restarts with the new layout and reloads this page,
        // which then resumes past this step.
        return
      }
      onNext(layout, variant)
    } catch (err) {
      setError(String(err))
    }
    setApplying(false)
  }

  if (applying) {
    return (
      <Card>
        <CardContent className="py-10">
          <LoadingText className="text-foreground justify-center">Applying keyboard layout…</LoadingText>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Keyboard layout</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && <p className="text-destructive text-sm">{error}</p>}
        <p className="text-muted-foreground text-sm">
          Used for the installer, the installed system, and the disk encryption prompt at boot.
        </p>
        <Input
          autoFocus
          aria-label="Search keyboard layouts"
          placeholder="Search layouts, e.g. german, fr, dvorak (↑/↓ to choose)"
          value={query}
          onChange={(event) => {
            const nextQuery = event.target.value
            setQuery(nextQuery)
            const needle = nextQuery.trim().toLowerCase()
            const first =
              layouts.find((item) => item.code.toLowerCase() === needle) ??
              layouts.find((item) => item.description.toLowerCase().startsWith(needle)) ??
              layouts.find((item) => item.description.toLowerCase().includes(needle))
            if (needle && first) {
              setLayout(first.code)
              setVariant("")
            }
          }}
          onKeyDown={handleSearchKey}
        />
        <div className="max-h-56 overflow-y-auto rounded-md border" role="listbox">
          {filtered.map((item) => (
            <button
              key={item.code}
              ref={item.code === layout ? selectedRef : undefined}
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={item.code === layout}
              className={`flex w-full justify-between px-3 py-1.5 text-left text-sm ${
                item.code === layout ? "bg-primary text-primary-foreground" : "hover:bg-accent"
              }`}
              onClick={() => {
                setLayout(item.code)
                setVariant("")
              }}
            >
              <span>{item.description}</span>
              <span className="font-mono text-xs opacity-70">{item.code}</span>
            </button>
          ))}
        </div>
        {selected && selected.variants.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="keyboard-variant">Variant</Label>
            <NativeSelect
              id="keyboard-variant"
              value={variant}
              onChange={(event) => setVariant(event.target.value)}
            >
              <option value="">Default</option>
              {selected.variants.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.description}
                </option>
              ))}
            </NativeSelect>
          </div>
        )}
        <div className="flex justify-end">
          <Button disabled={!selected} onClick={handleApply}>
            Next
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
