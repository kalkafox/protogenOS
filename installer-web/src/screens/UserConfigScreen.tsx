import { useEffect, useState } from "react"

import { getMirrorCountries, validateConfig } from "@/api/client"
import type { InstallConfig, MirrorCountry, SystemChoice, UserAccount } from "@/api/types"
import { TimezoneSearch } from "@/components/TimezoneSearch"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"
import { NativeSelect } from "@/components/ui/select"

const DEFAULT_SYSTEM: SystemChoice = {
  hostname: "protogenos",
  username: "",
  user_password: "",
  timezone: "UTC",
  locale: "en_US.UTF-8",
  grant_sudo: true,
  root_password: "",
  mirror_country: "",
  kernel_headers: false,
  additional_users: [],
}

export function UserConfigScreen({
  value,
  buildConfig,
  onBack,
  onNext,
}: {
  value: SystemChoice | null
  buildConfig: (system: SystemChoice) => InstallConfig
  onBack: () => void
  onNext: (system: SystemChoice) => void
}) {
  const [draft, setDraft] = useState<SystemChoice>(value ?? DEFAULT_SYSTEM)
  const [confirmPassword, setConfirmPassword] = useState(value?.user_password ?? "")
  const [countries, setCountries] = useState<MirrorCountry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    getMirrorCountries()
      .then((result) => setCountries(result.countries))
      .catch(() => setCountries([]))
  }, [])

  const update = <K extends keyof SystemChoice>(key: K, next: SystemChoice[K]) =>
    setDraft((current) => ({ ...current, [key]: next }))
  const updateUser = (index: number, patch: Partial<UserAccount>) =>
    update(
      "additional_users",
      draft.additional_users.map((user, position) => (position === index ? { ...user, ...patch } : user))
    )

  const handleNext = async () => {
    setError(null)
    if (draft.user_password !== confirmPassword) {
      setError("Passwords do not match")
      return
    }
    const system = { ...draft, root_password: draft.grant_sudo ? null : draft.root_password }
    setChecking(true)
    try {
      const result = await validateConfig(buildConfig(system))
      if (!result.valid) {
        setError(result.error ?? "Invalid configuration")
        return
      }
      onNext(system)
    } catch (err) {
      setError(String(err))
    } finally {
      setChecking(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>System and accounts</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && <p className="text-destructive text-sm">{error}</p>}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="hostname">Hostname</Label>
          <Input id="hostname" value={draft.hostname} onChange={(e) => update("hostname", e.target.value)} />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="username">Username</Label>
          <Input id="username" value={draft.username} onChange={(e) => update("username", e.target.value)} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              value={draft.user_password}
              onChange={(e) => update("user_password", e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="confirm-password">Confirm password</Label>
            <Input
              id="confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Checkbox
            id="grant-sudo"
            checked={draft.grant_sudo}
            onCheckedChange={(checked) => update("grant_sudo", checked === true)}
          />
          <Label htmlFor="grant-sudo" className="font-normal">
            Grant this user sudo access (recommended)
          </Label>
        </div>

        {!draft.grant_sudo && (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="root-password">Root password</Label>
            <Input
              id="root-password"
              type="password"
              value={draft.root_password ?? ""}
              onChange={(e) => update("root_password", e.target.value)}
            />
          </div>
        )}

        <div className="flex flex-col gap-2 rounded-md border p-3">
          <div className="flex items-center justify-between">
            <Label>Additional users</Label>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                update("additional_users", [...draft.additional_users, { username: "", password: "", sudo: false }])
              }
            >
              Add user
            </Button>
          </div>
          {draft.additional_users.length === 0 && (
            <p className="text-muted-foreground text-xs">Only the account above will be created.</p>
          )}
          {draft.additional_users.map((user, index) => (
            <div key={index} className="grid grid-cols-[1fr_1fr_auto_auto] items-center gap-2">
              <Input
                aria-label={`User ${index + 2} name`}
                placeholder="username"
                value={user.username}
                onChange={(e) => updateUser(index, { username: e.target.value })}
              />
              <Input
                aria-label={`User ${index + 2} password`}
                type="password"
                placeholder="password"
                value={user.password}
                onChange={(e) => updateUser(index, { password: e.target.value })}
              />
              <div className="flex items-center gap-1">
                <Checkbox
                  id={`user-${index}-sudo`}
                  checked={user.sudo}
                  onCheckedChange={(checked) => updateUser(index, { sudo: checked === true })}
                />
                <Label htmlFor={`user-${index}-sudo`} className="text-xs font-normal">
                  sudo
                </Label>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  update(
                    "additional_users",
                    draft.additional_users.filter((_, position) => position !== index)
                  )
                }
              >
                Remove
              </Button>
            </div>
          ))}
        </div>

        <TimezoneSearch value={draft.timezone} onChange={(zone) => update("timezone", zone)} />

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="locale">Locale</Label>
          <Input id="locale" value={draft.locale} onChange={(e) => update("locale", e.target.value)} />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mirror-country">Package mirrors</Label>
          <NativeSelect
            id="mirror-country"
            value={draft.mirror_country}
            onChange={(e) => update("mirror_country", e.target.value)}
          >
            <option value="">Automatic (fastest worldwide)</option>
            {countries.map((country) => (
              <option key={country.code} value={country.name}>
                {country.name} ({country.count})
              </option>
            ))}
          </NativeSelect>
        </div>

        <div className="flex items-center gap-2">
          <Checkbox
            id="kernel-headers"
            checked={draft.kernel_headers}
            onCheckedChange={(checked) => update("kernel_headers", checked === true)}
          />
          <Label htmlFor="kernel-headers" className="font-normal">
            Install kernel headers (needed for DKMS drivers such as VirtualBox or NVIDIA modules)
          </Label>
        </div>

        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button aria-busy={checking} disabled={checking} onClick={handleNext}>
            {checking && <Spinner />}
            {checking ? "Checking…" : "Next"}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
