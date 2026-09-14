import { useCallback, useEffect, useState } from "react"

import { connectWifi, getNetwork, scanWifi } from "@/api/client"
import type { WifiNetwork } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"

type Phase = "checking" | "offline" | "online"

export function NetworkScreen({ onNext }: { onNext: () => void }) {
  const [phase, setPhase] = useState<Phase>("checking")
  const [devices, setDevices] = useState<string[]>([])
  const [device, setDevice] = useState("")
  const [networks, setNetworks] = useState<WifiNetwork[]>([])
  const [selected, setSelected] = useState("")
  const [passphrase, setPassphrase] = useState("")
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const scan = useCallback(async (target: string) => {
    setBusy("Scanning for networks…")
    setError(null)
    try {
      const result = await scanWifi(target)
      setNetworks(result.networks)
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(null)
    }
  }, [])

  const check = useCallback(
    async (autoAdvance: boolean) => {
      setPhase("checking")
      setError(null)
      try {
        const status = await getNetwork()
        setDevices(status.devices)
        if (status.online) {
          setPhase("online")
          if (autoAdvance) onNext()
          return
        }
        setPhase("offline")
        const first = status.devices[0]
        if (first) {
          setDevice((current) => current || first)
          await scan(first)
        }
      } catch (err) {
        setError(String(err))
        setPhase("offline")
      }
    },
    [onNext, scan]
  )

  useEffect(() => {
    check(true)
  }, [check])

  const network = networks.find((item) => item.ssid === selected)
  const needsPassphrase = network?.security === "psk" && !network.known

  const handleConnect = async () => {
    if (!network) return
    setBusy(`Connecting to ${network.ssid}…`)
    setError(null)
    try {
      const result = await connectWifi(device, network.ssid, needsPassphrase ? passphrase : "")
      setPassphrase("")
      if (result.online) {
        setPhase("online")
      } else {
        setError(`Joined ${network.ssid}, but the internet is not reachable yet.`)
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Internet connection</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="text-muted-foreground text-sm">
          Packages are downloaded during installation, so an internet connection is required.
        </p>
        {error && <p className="text-destructive text-sm">{error}</p>}
        {phase === "checking" && <p className="text-sm">Checking connection…</p>}
        {phase === "online" && <p className="text-sm font-medium">Connected to the internet.</p>}

        {phase === "offline" && devices.length === 0 && (
          <p className="text-sm">
            No Wi-Fi adapter was found. Plug in an Ethernet cable or USB tethering, then check
            again.
          </p>
        )}

        {phase === "offline" && devices.length > 0 && (
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <Label>Wi-Fi networks on {device}</Label>
              <Button
                variant="outline"
                size="sm"
                disabled={busy !== null}
                onClick={() => scan(device)}
              >
                Rescan
              </Button>
            </div>
            {networks.length === 0 && !busy && (
              <p className="text-muted-foreground text-sm">No networks found.</p>
            )}
            <RadioGroup
              value={selected}
              onValueChange={setSelected}
              className="max-h-64 overflow-y-auto"
            >
              {networks.map((item) => (
                <div key={item.ssid} className="flex items-center gap-3 rounded-md border p-2">
                  <RadioGroupItem value={item.ssid} id={`wifi-${item.ssid}`} />
                  <Label htmlFor={`wifi-${item.ssid}`} className="flex flex-1 justify-between font-normal">
                    <span>{item.ssid}</span>
                    <span className="text-muted-foreground text-xs">
                      {item.security === "open" ? "open" : "secured"} · {item.signal} dBm
                    </span>
                  </Label>
                </div>
              ))}
            </RadioGroup>
            {needsPassphrase && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="wifi-passphrase">Passphrase</Label>
                <Input
                  id="wifi-passphrase"
                  type="password"
                  value={passphrase}
                  onChange={(event) => setPassphrase(event.target.value)}
                />
              </div>
            )}
            <Button
              disabled={!network || busy !== null || (needsPassphrase && passphrase.length < 8)}
              onClick={handleConnect}
            >
              Connect
            </Button>
          </div>
        )}

        {busy && <p className="text-muted-foreground text-sm">{busy}</p>}

        <div className="flex justify-between">
          <Button variant="outline" disabled={busy !== null} onClick={() => check(false)}>
            Check again
          </Button>
          <Button disabled={phase !== "online"} onClick={onNext}>
            Next
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
