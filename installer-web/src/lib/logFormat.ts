// Turns install log lines into styled segments for LogView. Lines are
// classified by the prefixes the backend and common tools print, and any
// ANSI SGR colors a tool emits are rendered instead of shown as escapes.

export type LogLineKind = "step" | "command" | "error" | "warning" | "heading" | "note" | "output"

export interface LogSegment {
  text: string
  color?: string
  bold?: boolean
}

export interface LogLine {
  kind: LogLineKind
  segments: LogSegment[]
}

const STEP_PREFIX = "[protogenos] step "

const ERROR_PATTERN = /^\s*(?:==> )?(?:error|fatal|ERROR|FATAL)\b:?/
const WARNING_PATTERN = /^\s*(?:\[protogenos\] |==> )?(?:warning|WARNING)\b:?/
// pacman section headers (":: Synchronizing…") and mkinitcpio/makepkg ("==> Building…").
const HEADING_PATTERN = /^\s*(?:::|==>)\s/
const NOTE_PATTERN = /^\s*\((?:dry-run)|^Package cache: /

export function classifyLine(text: string): LogLineKind {
  if (text.startsWith(STEP_PREFIX)) return "step"
  if (text.startsWith("+ ")) return "command"
  if (ERROR_PATTERN.test(text)) return "error"
  if (WARNING_PATTERN.test(text)) return "warning"
  if (HEADING_PATTERN.test(text)) return "heading"
  if (NOTE_PATTERN.test(text)) return "note"
  return "output"
}

// Standard and bright ANSI colors, tuned to stay readable on the Carbon/Raised
// log background rather than matching a terminal's defaults exactly.
const ANSI_COLORS = [
  "#6f6469", // black
  "#ff5f73", // red
  "#7fd88f", // green
  "#f2c14e", // yellow
  "#6fb3ff", // blue
  "#d28cff", // magenta
  "#5fd7d7", // cyan
  "#e6dfe1", // white
]
const ANSI_BRIGHT_COLORS = ["#9a8f94", "#ff8795", "#a3ecb0", "#ffd97a", "#9ccbff", "#e3b3ff", "#8ceaea", "#ffffff"]

// Control sequences: CSI (colors, cursor movement) and OSC (titles, links).
// eslint-disable-next-line no-control-regex
const ESCAPE_PATTERN = /\x1b\[([0-9;?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g

export function parseAnsi(text: string): LogSegment[] {
  const segments: LogSegment[] = []
  let color: string | undefined
  let bold = false
  let last = 0

  const push = (chunk: string) => {
    if (!chunk) return
    const previous = segments.at(-1)
    if (previous && previous.color === color && Boolean(previous.bold) === bold) {
      previous.text += chunk
    } else {
      segments.push({ text: chunk, ...(color ? { color } : {}), ...(bold ? { bold } : {}) })
    }
  }

  for (const match of text.matchAll(ESCAPE_PATTERN)) {
    push(text.slice(last, match.index))
    last = match.index + match[0].length
    if (match[2] !== "m") continue // non-color CSI and OSC sequences are dropped

    const codes = (match[1] || "0").split(";").map((code) => Number(code || "0"))
    for (let i = 0; i < codes.length; i++) {
      const code = codes[i]
      if (code === 0) {
        color = undefined
        bold = false
      } else if (code === 1) bold = true
      else if (code === 22) bold = false
      else if (code >= 30 && code <= 37) color = ANSI_COLORS[code - 30]
      else if (code >= 90 && code <= 97) color = ANSI_BRIGHT_COLORS[code - 90]
      else if (code === 39) color = undefined
      else if (code === 38 || code === 48) {
        // Extended colors: 38;5;n or 38;2;r;g;b. Foreground 256-color maps its
        // first 16 entries; other extended colors are skipped but consumed.
        const mode = codes[i + 1]
        if (code === 38 && mode === 5) {
          const index = codes[i + 2]
          if (index < 8) color = ANSI_COLORS[index]
          else if (index < 16) color = ANSI_BRIGHT_COLORS[index - 8]
        } else if (code === 38 && mode === 2) {
          const [r, g, b] = codes.slice(i + 2, i + 5)
          color = `rgb(${r}, ${g}, ${b})`
        }
        i += mode === 5 ? 2 : mode === 2 ? 4 : 1
      }
    }
  }
  push(text.slice(last))
  return segments
}

export function formatLogLine(text: string): LogLine {
  const segments = parseAnsi(text)
  const plain = segments.map((segment) => segment.text).join("")
  return { kind: classifyLine(plain), segments }
}
