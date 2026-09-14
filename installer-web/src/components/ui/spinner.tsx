import * as React from "react"

import { cn } from "@/lib/utils"

// Protogen visor LEDs: a 3×3 matrix whose edge cells light up in a clockwise
// chase; the center stays dimly lit. Cells use the current text color.
const CHASE_ORDER = [0, 1, 2, 5, 8, 7, 6, 3]
const CHASE_STEP_SECONDS = 0.1

function Spinner({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="spinner"
      aria-hidden="true"
      className={cn("grid size-4 shrink-0 grid-cols-3 gap-px", className)}
      {...props}
    >
      {Array.from({ length: 9 }, (_, cell) => {
        const order = CHASE_ORDER.indexOf(cell)
        return (
          <span
            key={cell}
            className={cn("rounded-[1px] bg-current", order < 0 ? "opacity-25" : "led-chase")}
            style={
              order < 0
                ? undefined
                : { animationDelay: `${(order - CHASE_ORDER.length) * CHASE_STEP_SECONDS}s` }
            }
          />
        )
      })}
    </span>
  )
}

// A spinner with a visible label, announced to screen readers as it changes.
function LoadingText({ className, children, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      role="status"
      className={cn("text-muted-foreground flex items-center gap-2 text-sm", className)}
      {...props}
    >
      <Spinner className="text-ring" />
      <span>{children}</span>
    </p>
  )
}

export { LoadingText, Spinner }
