import { useState, type ReactNode } from "react"

import type { FeatureChoice, StorageChoice, SystemInfo } from "@/api/types"
import { ChoiceList } from "@/components/ChoiceList"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"

// Recommended defaults for this machine, persona, and storage choice.
function defaultFeatures(persona: string, storage: StorageChoice, system: SystemInfo): FeatureChoice {
  return {
    snapshots: storage.filesystem === "btrfs" && storage.btrfs_subvolumes,
    flatpak: persona !== "minimal",
    gaming_tweaks: persona === "gamer",
    nvidia_driver: system.hardware.nvidia_open_supported ? "nvidia-open" : "nouveau",
    fingerprint: system.features.fingerprint_reader,
    tpm2_unlock: false,
    secure_boot: false,
  }
}

function Toggle({
  id,
  checked,
  disabled,
  title,
  children,
  onChange,
}: {
  id: string
  checked: boolean
  disabled?: boolean
  title: ReactNode
  children: ReactNode
  onChange: (checked: boolean) => void
}) {
  return (
    <div className={`flex items-start gap-3 rounded-md border p-3 ${disabled ? "opacity-50" : ""}`}>
      <Checkbox
        id={id}
        checked={checked && !disabled}
        disabled={disabled}
        onCheckedChange={(next) => onChange(next === true)}
        className="mt-0.5"
      />
      <Label htmlFor={id} className="flex flex-col items-start gap-1 font-normal">
        <span className="font-medium">{title}</span>
        <span className="text-muted-foreground text-xs">{children}</span>
      </Label>
    </div>
  )
}

export function FeaturesScreen({
  persona,
  storage,
  system,
  value,
  onBack,
  onNext,
}: {
  persona: string
  storage: StorageChoice
  system: SystemInfo
  value: FeatureChoice | null
  onBack: () => void
  onNext: (choice: FeatureChoice) => void
}) {
  const [choice, setChoice] = useState<FeatureChoice>(value ?? defaultFeatures(persona, storage, system))
  const update = <K extends keyof FeatureChoice>(key: K, next: FeatureChoice[K]) =>
    setChoice((current) => ({ ...current, [key]: next }))

  const { hardware, features } = system
  const snapshotsAvailable = storage.filesystem === "btrfs" && storage.btrfs_subvolumes
  const secureBootLoader = storage.bootloader === "systemd-boot" || storage.bootloader === "limine"
  const secureBootAvailable = system.firmware === "uefi" && secureBootLoader
  const tpmAvailable = storage.encrypt && features.tpm2

  let secureBootNote: string
  if (system.firmware !== "uefi") secureBootNote = "Requires UEFI boot."
  else if (!secureBootLoader) secureBootNote = "Choose systemd-boot or Limine on the previous screen to use Secure Boot."
  else if (features.secure_boot_setup_mode)
    secureBootNote = "Creates your own keys, signs the bootloader and kernel, and enrolls the keys (Microsoft keys kept). Enable Secure Boot in firmware afterwards."
  else
    secureBootNote = "Creates keys and signs boot files. Firmware is not in Setup Mode, so you will enroll the keys yourself after installing."

  let tpmNote: string
  if (!storage.encrypt) tpmNote = "Requires disk encryption."
  else if (!features.tpm2) tpmNote = "No TPM 2.0 chip detected."
  else
    tpmNote = "Unlocks the disk automatically while Secure Boot settings are unchanged. The passphrase keeps working as a fallback."

  const finish = () =>
    onNext({
      ...choice,
      snapshots: snapshotsAvailable && choice.snapshots,
      secure_boot: secureBootAvailable && choice.secure_boot,
      tpm2_unlock: tpmAvailable && choice.tpm2_unlock,
      nvidia_driver: hardware.nvidia_open_supported ? choice.nvidia_driver : "nouveau",
    })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Extras</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Toggle
          id="snapshots"
          checked={choice.snapshots}
          disabled={!snapshotsAvailable}
          title="System snapshots"
          onChange={(next) => update("snapshots", next)}
        >
          {snapshotsAvailable
            ? `Snapper takes a snapshot before and after every package change${
                storage.bootloader === "grub" ? ", and GRUB can boot into older snapshots" : ""
              }.`
            : "Requires Btrfs with subvolumes."}
        </Toggle>

        <Toggle
          id="flatpak"
          checked={choice.flatpak}
          title="Flatpak apps from Flathub"
          onChange={(next) => update("flatpak", next)}
        >
          Install sandboxed apps from Flathub, including through Discover.
        </Toggle>

        <Toggle
          id="gaming"
          checked={choice.gaming_tweaks}
          title="Gaming tweaks"
          onChange={(next) => update("gaming_tweaks", next)}
        >
          Feral GameMode for every user, and no split-lock slowdown in older games.
        </Toggle>

        {hardware.nvidia_open_supported !== null && (
          <div className="flex flex-col gap-2 pt-1">
            <Label>NVIDIA graphics driver</Label>
            <ChoiceList
              name="nvidia"
              value={hardware.nvidia_open_supported ? choice.nvidia_driver : "nouveau"}
              onChange={(next) => update("nvidia_driver", next as FeatureChoice["nvidia_driver"])}
              choices={[
                {
                  value: "nvidia-open",
                  title: "NVIDIA open kernel modules (recommended)",
                  description: hardware.nvidia_open_supported
                    ? `Full performance and CUDA.${hardware.gpus.length > 1 ? " Use prime-run to start apps on the NVIDIA GPU." : ""}`
                    : "Needs a GTX 16xx, RTX 20xx, or newer card.",
                  disabled: !hardware.nvidia_open_supported,
                },
                {
                  value: "nouveau",
                  title: "Nouveau",
                  description: "Open-source community driver; lower performance on most cards.",
                },
              ]}
            />
          </div>
        )}

        {features.fingerprint_reader && (
          <Toggle
            id="fingerprint"
            checked={choice.fingerprint}
            title="Fingerprint login"
            onChange={(next) => update("fingerprint", next)}
          >
            Installs fprintd. Enroll a finger in System Settings → Users after installing.
          </Toggle>
        )}

        <Toggle
          id="tpm2"
          checked={choice.tpm2_unlock}
          disabled={!tpmAvailable}
          title="Unlock the disk with the TPM"
          onChange={(next) => update("tpm2_unlock", next)}
        >
          {tpmNote}
        </Toggle>

        <Toggle
          id="secure-boot"
          checked={choice.secure_boot}
          disabled={!secureBootAvailable}
          title="Secure Boot"
          onChange={(next) => update("secure_boot", next)}
        >
          {secureBootNote}
        </Toggle>

        {choice.secure_boot && choice.tpm2_unlock && tpmAvailable && secureBootAvailable && (
          <p className="text-muted-foreground text-xs">
            Turning on Secure Boot changes what the TPM measures, so the disk asks for its passphrase
            until TPM unlock is enrolled again. The finish screen shows the command.
          </p>
        )}

        <div className="flex justify-between pt-2">
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button onClick={finish}>Next</Button>
        </div>
      </CardContent>
    </Card>
  )
}
