import { useEffect, useState } from "react"

import { getPrefetch } from "@/api/client"
import type { PrefetchStatus } from "@/api/types"

// Follow the background package download while it runs. `key` changes when a
// new plan restarts it; polling stops once it settles or `active` is false.
export function usePrefetch(key: unknown, active: boolean): PrefetchStatus | null {
  const [status, setStatus] = useState<PrefetchStatus | null>(null)

  useEffect(() => {
    if (!active || key === null) return
    let timer: number | undefined
    let cancelled = false
    const poll = () => {
      getPrefetch()
        .then((next) => {
          if (cancelled) return
          setStatus(next)
          if (next.state === "preparing" || next.state === "downloading") {
            timer = window.setTimeout(poll, 1500)
          }
        })
        .catch(() => {
          if (!cancelled) setStatus(null)
        })
    }
    poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [key, active])

  return status
}
