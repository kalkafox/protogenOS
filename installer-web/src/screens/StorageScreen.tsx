import { useState } from "react"

import type { Bootloader, DiskLayoutKind, Filesystem, Firmware, StorageChoice } from "@/api/types"
import { ChoiceList } from "@/components/ChoiceList"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

const DEFAULT_STORAGE: StorageChoice = {
  filesystem: "btrfs",
  encrypt: false,
  encryption_passphrase: null,
  swap: "zram",
  bootloader: "grub",
}

function isConsoleTypable(value: string): boolean {
  return [...value].every((character) => {
    const code = character.charCodeAt(0)
    return code >= 32 && code <= 126
  })
}

export function StorageScreen({
  firmware,
  diskLayout,
  keyboardLayout,
  value,
  onBack,
  onNext,
}: {
  firmware: Firmware
  diskLayout: DiskLayoutKind
  keyboardLayout: string
  value: StorageChoice | null
  onBack: () => void
  onNext: (choice: StorageChoice) => void
}) {
  const [choice, setChoice] = useState<StorageChoice>(value ?? DEFAULT_STORAGE)
  const [confirmation, setConfirmation] = useState(value?.encryption_passphrase ?? "")
  const update = <K extends keyof StorageChoice>(key: K, next: StorageChoice[K]) =>
    setChoice((current) => ({ ...current, [key]: next }))

  const uefi = firmware === "uefi"
  const passphrase = choice.encryption_passphrase ?? ""
  let passphraseError: string | null = null
  if (choice.encrypt) {
    if (passphrase.length < 8) passphraseError = "Use at least 8 characters."
    else if (!isConsoleTypable(passphrase))
      passphraseError = "Use only plain ASCII characters; the boot prompt can't type others."
    else if (passphrase !== confirmation) passphraseError = "Passphrases do not match."
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Storage and boot</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <Label>Filesystem</Label>
          <ChoiceList
            name="filesystem"
            value={choice.filesystem}
            onChange={(next) => update("filesystem", next as Filesystem)}
            choices={[
              {
                value: "btrfs",
                title: "Btrfs (recommended)",
                description: "Compressed, with @, @home, @log, @pkg and @snapshots subvolumes ready for snapshots.",
              },
              { value: "ext4", title: "ext4", description: "The classic, battle-tested Linux filesystem." },
              { value: "xfs", title: "XFS", description: "Fast for large files; cannot be shrunk later." },
              { value: "f2fs", title: "F2FS", description: "Designed for flash storage (SSDs, SD cards)." },
            ]}
          />
        </div>

        <div className="flex flex-col gap-3 rounded-md border p-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="encrypt"
              checked={choice.encrypt}
              onCheckedChange={(checked) => update("encrypt", checked === true)}
            />
            <Label htmlFor="encrypt" className="font-normal">
              Encrypt the system (LUKS2) — a passphrase is required at every boot
            </Label>
          </div>
          {choice.encrypt && (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="passphrase">Encryption passphrase</Label>
                <Input
                  id="passphrase"
                  type="password"
                  value={passphrase}
                  onChange={(event) => update("encryption_passphrase", event.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="passphrase-confirm">Confirm passphrase</Label>
                <Input
                  id="passphrase-confirm"
                  type="password"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                />
              </div>
              <p className="text-muted-foreground text-xs">
                Typed with the <span className="font-mono">{keyboardLayout}</span> layout, both
                here and at the boot prompt. If you forget it, the data cannot be recovered.
              </p>
              {passphraseError && <p className="text-destructive text-xs">{passphraseError}</p>}
            </>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Checkbox
            id="zram"
            checked={choice.swap === "zram"}
            onCheckedChange={(checked) => update("swap", checked === true ? "zram" : "none")}
          />
          <Label htmlFor="zram" className="font-normal">
            Compressed swap in RAM (zram)
          </Label>
        </div>

        <div className="flex flex-col gap-2">
          <Label>Bootloader</Label>
          <ChoiceList
            name="bootloader"
            value={choice.bootloader}
            onChange={(next) => update("bootloader", next as Bootloader)}
            choices={[
              {
                value: "grub",
                title: "GRUB",
                description:
                  diskLayout === "erase"
                    ? "Works everywhere, including BIOS systems."
                    : "Recommended for dual boot: detects Windows and other systems automatically.",
              },
              {
                value: "systemd-boot",
                title: "systemd-boot",
                description: uefi ? "Minimal and fast. UEFI only." : "Requires UEFI boot.",
                disabled: !uefi,
              },
              {
                value: "limine",
                title: "Limine",
                description: uefi ? "Modern, lightweight, and quick. UEFI only." : "Requires UEFI boot.",
                disabled: !uefi,
              },
            ]}
          />
        </div>

        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button
            disabled={passphraseError !== null}
            onClick={() =>
              onNext({
                ...choice,
                encryption_passphrase: choice.encrypt ? passphrase : null,
                bootloader: uefi ? choice.bootloader : "grub",
              })
            }
          >
            Next
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
