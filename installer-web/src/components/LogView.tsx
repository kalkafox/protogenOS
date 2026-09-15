import { useEffect, useMemo, useRef } from "react"

import { ScrollArea } from "@/components/ui/scroll-area"
import { formatLogLine, type LogLineKind, type LogSegment } from "@/lib/logFormat"
import { cn } from "@/lib/utils"

// Base style per line type. ANSI colors from a tool override the line color.
const KIND_CLASSES: Record<LogLineKind, string> = {
  step: "text-ring mt-2 font-semibold first:mt-0",
  command: "text-foreground",
  error: "-mx-1 rounded-sm bg-[#ff6b7f]/10 px-1 font-semibold text-[#ff6b7f]",
  warning: "text-destructive",
  heading: "text-foreground font-medium",
  note: "text-muted-foreground/70 italic",
  output: "text-muted-foreground",
}

// Scrollable, color-coded install log that follows new output.
export function LogView({ lines, className }: { lines: string[]; className?: string }) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const formatted = useMemo(() => lines.map(formatLogLine), [lines])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" })
  }, [lines])

  return (
    <ScrollArea className={cn("bg-muted rounded-md border p-3", className)}>
      <pre className="font-mono text-xs whitespace-pre-wrap">
        {formatted.map((line, index) => (
          <div key={index} className={KIND_CLASSES[line.kind]}>
            {line.kind === "command" && <span className="text-ring">+ </span>}
            {(line.kind === "command" ? stripCommandPrefix(line.segments) : line.segments).map(
              (segment, segmentIndex) => (
                <span
                  key={segmentIndex}
                  className={segment.bold ? "font-semibold" : undefined}
                  style={segment.color ? { color: segment.color } : undefined}
                >
                  {segment.text}
                </span>
              ),
            )}
            {/* Keep blank lines from collapsing to zero height. */}
            {line.segments.length === 0 && " "}
          </div>
        ))}
        <div ref={bottomRef} />
      </pre>
    </ScrollArea>
  )
}

function stripCommandPrefix(segments: LogSegment[]) {
  const [first, ...rest] = segments
  if (!first?.text.startsWith("+ ")) return segments
  return [{ ...first, text: first.text.slice(2) }, ...rest]
}
