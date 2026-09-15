import { useState } from "react"

import { getGithubKeys, validateConfig } from "@/api/client"
import type { InstallConfig, ServerChoice } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"
import { EMPTY_SERVER, SSH_KEY_PATTERN } from "@/lib/server"

function keyLabel(key: string): string {
  const [type, material = "", ...comment] = key.split(" ")
  const fingerprint = material.length > 16 ? `${material.slice(0, 8)}…${material.slice(-8)}` : material
  return [type, fingerprint, comment.join(" ")].filter(Boolean).join(" ")
}

export function ServerScreen({
  username,
  value,
  buildConfig,
  onBack,
  onNext,
}: {
  username: string
  value: ServerChoice | null
  buildConfig: (server: ServerChoice) => InstallConfig
  onBack: () => void
  onNext: (server: ServerChoice) => void
}) {
  const [draft, setDraft] = useState<ServerChoice>(value ?? EMPTY_SERVER)
  const [pasted, setPasted] = useState("")
  const [githubUser, setGithubUser] = useState("")
  const [staticEnabled, setStaticEnabled] = useState(Boolean(value?.static_address))
  const [dnsText, setDnsText] = useState(value?.static_dns.join(" ") ?? "")
  const [keyError, setKeyError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [checking, setChecking] = useState(false)

  const update = <K extends keyof ServerChoice>(key: K, next: ServerChoice[K]) =>
    setDraft((current) => ({ ...current, [key]: next }))
  const addKeys = (keys: string[]) =>
    setDraft((current) => ({
      ...current,
      ssh_authorized_keys: [...new Set([...current.ssh_authorized_keys, ...keys])],
    }))

  const addPasted = () => {
    const lines = pasted
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"))
    const invalid = lines.find((line) => !SSH_KEY_PATTERN.test(line))
    if (lines.length === 0 || invalid) {
      setKeyError(
        invalid
          ? `Not an SSH public key: ${invalid.slice(0, 40)}…`
          : "Paste a public key, such as the contents of ~/.ssh/id_ed25519.pub."
      )
      return
    }
    setKeyError(null)
    addKeys(lines)
    setPasted("")
  }

  const importGithub = async () => {
    setKeyError(null)
    setImporting(true)
    try {
      const result = await getGithubKeys(githubUser.trim())
      addKeys(result.keys)
      setGithubUser("")
    } catch (err) {
      setKeyError(err instanceof Error ? err.message : String(err))
    } finally {
      setImporting(false)
    }
  }

  const handleNext = async () => {
    setError(null)
    const server: ServerChoice = staticEnabled
      ? { ...draft, static_dns: dnsText.split(/[\s,]+/).filter(Boolean) }
      : { ...draft, static_address: "", static_gateway: "", static_dns: [], static_interface: "" }
    setChecking(true)
    try {
      const result = await validateConfig(buildConfig(server), "server")
      if (!result.valid) {
        setError(result.error ?? "Invalid configuration")
        return
      }
      onNext(server)
    } catch (err) {
      setError(String(err))
    } finally {
      setChecking(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Server access</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {error && <p className="text-destructive text-sm">{error}</p>}

        <section className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium">SSH keys for {username || "the administrator"}</p>
            <p className="text-muted-foreground text-xs">
              Password logins over SSH are turned off, so add at least one public key. SSH and the
              firewall are always enabled on servers.
            </p>
          </div>

          {draft.ssh_authorized_keys.length > 0 && (
            <ul className="flex flex-col gap-1.5">
              {draft.ssh_authorized_keys.map((key) => (
                <li key={key} className="flex items-center justify-between gap-2 rounded-md border px-3 py-1.5">
                  <span className="truncate font-mono text-xs" title={key}>
                    {keyLabel(key)}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      update(
                        "ssh_authorized_keys",
                        draft.ssh_authorized_keys.filter((item) => item !== key)
                      )
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ssh-paste">Paste public keys</Label>
            <textarea
              id="ssh-paste"
              rows={3}
              value={pasted}
              placeholder="ssh-ed25519 AAAA… you@laptop"
              onChange={(e) => setPasted(e.target.value)}
              className="border-input placeholder:text-muted-foreground focus-visible:ring-ring w-full rounded-md border bg-transparent px-3 py-2 font-mono text-xs shadow-xs outline-none focus-visible:ring-2"
            />
            <div className="flex justify-end">
              <Button variant="outline" size="sm" disabled={!pasted.trim()} onClick={addPasted}>
                Add key
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="github-user">Or import from a GitHub account</Label>
            <div className="flex gap-2">
              <Input
                id="github-user"
                value={githubUser}
                placeholder="GitHub username"
                onChange={(e) => setGithubUser(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && githubUser.trim()) importGithub()
                }}
              />
              <Button
                variant="outline"
                aria-busy={importing}
                disabled={!githubUser.trim() || importing}
                onClick={importGithub}
              >
                {importing && <Spinner />}
                Import
              </Button>
            </div>
          </div>

          {keyError && <p className="text-destructive text-sm">{keyError}</p>}
        </section>

        <section className="flex flex-col gap-3">
          <div className="flex items-start gap-2">
            <Checkbox
              id="static-ip"
              className="mt-0.5"
              checked={staticEnabled}
              onCheckedChange={(checked) => setStaticEnabled(checked === true)}
            />
            <Label htmlFor="static-ip" className="flex flex-col items-start gap-1 font-normal">
              <span className="font-medium">Use a static IP address</span>
              <span className="text-muted-foreground text-xs">
                Otherwise the wired connection gets its address from DHCP.
              </span>
            </Label>
          </div>

          {staticEnabled && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="static-address">Address with prefix</Label>
                <Input
                  id="static-address"
                  value={draft.static_address}
                  placeholder="192.168.1.10/24"
                  onChange={(e) => update("static_address", e.target.value.trim())}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="static-gateway">Gateway</Label>
                <Input
                  id="static-gateway"
                  value={draft.static_gateway}
                  placeholder="192.168.1.1"
                  onChange={(e) => update("static_gateway", e.target.value.trim())}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="static-dns">DNS servers</Label>
                <Input
                  id="static-dns"
                  value={dnsText}
                  placeholder="1.1.1.1 9.9.9.9"
                  onChange={(e) => setDnsText(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="static-interface">Interface</Label>
                <Input
                  id="static-interface"
                  value={draft.static_interface}
                  placeholder="Any wired interface"
                  onChange={(e) => update("static_interface", e.target.value.trim())}
                />
              </div>
            </div>
          )}
        </section>

        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button
            aria-busy={checking}
            disabled={checking || draft.ssh_authorized_keys.length === 0}
            onClick={handleNext}
          >
            {checking && <Spinner />}
            {checking ? "Checking…" : "Next"}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
