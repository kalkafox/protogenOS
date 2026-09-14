import { useCallback, useEffect, useState } from "react"

import { getKeyboard, getSystem, resolvePlan, startInstall } from "@/api/client"
import type {
  DiskChoice,
  FeatureChoice,
  Firmware,
  InstallConfig,
  InstallPlan,
  StorageChoice,
  SystemChoice,
  SystemInfo,
} from "@/api/types"

import { LoadingText } from "@/components/ui/spinner"

import { KeyboardScreen } from "@/screens/KeyboardScreen"
import { NetworkScreen } from "@/screens/NetworkScreen"
import { PersonaScreen } from "@/screens/PersonaScreen"
import { OptionsScreen } from "@/screens/OptionsScreen"
import { AurConfirmScreen } from "@/screens/AurConfirmScreen"
import { DiskScreen } from "@/screens/DiskScreen"
import { StorageScreen } from "@/screens/StorageScreen"
import { FeaturesScreen } from "@/screens/FeaturesScreen"
import { UserConfigScreen } from "@/screens/UserConfigScreen"
import { ReviewScreen } from "@/screens/ReviewScreen"
import { ProgressScreen } from "@/screens/ProgressScreen"
import { DoneScreen } from "@/screens/DoneScreen"
import { ErrorScreen } from "@/screens/ErrorScreen"

type Step =
  | "loading"
  | "keyboard"
  | "network"
  | "persona"
  | "options"
  | "aur"
  | "disk"
  | "storage"
  | "features"
  | "config"
  | "review"
  | "progress"
  | "done"
  | "error"

function App() {
  const [step, setStep] = useState<Step>("loading")
  const [firmware, setFirmware] = useState<Firmware>("uefi")
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
  const [keyboard, setKeyboardChoice] = useState({ layout: "us", variant: "" })
  const [persona, setPersona] = useState("")
  const [selections, setSelections] = useState<Record<string, string[]>>({})
  const [plan, setPlan] = useState<InstallPlan | null>(null)
  const [diskChoice, setDiskChoice] = useState<DiskChoice | null>(null)
  const [storage, setStorage] = useState<StorageChoice | null>(null)
  const [features, setFeatures] = useState<FeatureChoice | null>(null)
  const [system, setSystem] = useState<SystemChoice | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  // The kiosk reloads this page after a keyboard change; resume past it.
  useEffect(() => {
    Promise.all([getSystem(), getKeyboard()])
      .then(([info, current]) => {
        setFirmware(info.firmware)
        setSystemInfo(info)
        setKeyboardChoice({ layout: current.layout, variant: current.variant })
        setStep(current.applied ? "network" : "keyboard")
      })
      .catch(() => setStep("keyboard"))
  }, [])

  const goToPersona = useCallback(() => setStep("persona"), [])
  const handleDone = useCallback((installWarnings: string[]) => {
    setWarnings(installWarnings)
    setStep("done")
  }, [])
  const handleError = useCallback((message: string) => {
    setError(message)
    setStep("error")
  }, [])

  const buildConfig = useCallback(
    (systemChoice: SystemChoice): InstallConfig => {
      if (!diskChoice || !storage || !features) throw new Error("disk, storage, and extras must be chosen first")
      return {
        ...diskChoice,
        ...storage,
        ...features,
        ...systemChoice,
        firmware,
        keyboard_layout: keyboard.layout,
        keyboard_variant: keyboard.variant,
      }
    },
    [diskChoice, storage, features, firmware, keyboard]
  )
  const config = system && diskChoice && storage && features ? buildConfig(system) : null

  const handleOptionsNext = useCallback(
    async (nextSelections: Record<string, string[]>) => {
      setSelections(nextSelections)
      try {
        const resolved = await resolvePlan(persona, nextSelections)
        setPlan(resolved)
        setStep(resolved.aur_packages.length > 0 ? "aur" : "disk")
      } catch (err) {
        setError(String(err))
        setStep("error")
      }
    },
    [persona]
  )

  const handleInstall = useCallback(async () => {
    if (!plan || !config) return
    try {
      await startInstall(plan, config, plan.aur_packages.length > 0)
      setStep("progress")
    } catch (err) {
      setError(String(err))
      setStep("error")
    }
  }, [plan, config])

  return (
    <div className="mx-auto flex min-h-svh max-w-xl flex-col justify-center px-4 py-10">
      <h1 className="mb-6 text-center text-2xl font-semibold">protogenOS Installer</h1>

      {step === "loading" && <LoadingText className="justify-center">Loading…</LoadingText>}

      {step === "keyboard" && (
        <KeyboardScreen
          onNext={(layout, variant) => {
            setKeyboardChoice({ layout, variant })
            setStep("network")
          }}
        />
      )}

      {step === "network" && <NetworkScreen onNext={goToPersona} />}

      {step === "persona" && (
        <PersonaScreen
          value={persona}
          onNext={(value) => {
            setPersona(value)
            setStep("options")
          }}
        />
      )}

      {step === "options" && (
        <OptionsScreen
          persona={persona}
          value={selections}
          onBack={() => setStep("persona")}
          onNext={handleOptionsNext}
        />
      )}

      {step === "aur" && plan && (
        <AurConfirmScreen
          packages={plan.aur_packages}
          onBack={() => setStep("options")}
          onNext={() => setStep("disk")}
        />
      )}

      {step === "disk" && (
        <DiskScreen
          firmware={firmware}
          value={diskChoice}
          onBack={() => setStep(plan && plan.aur_packages.length > 0 ? "aur" : "options")}
          onNext={(choice) => {
            setDiskChoice(choice)
            setStep("storage")
          }}
        />
      )}

      {step === "storage" && diskChoice && (
        <StorageScreen
          firmware={firmware}
          diskLayout={diskChoice.disk_layout}
          keyboardLayout={keyboard.variant ? `${keyboard.layout} (${keyboard.variant})` : keyboard.layout}
          value={storage}
          onBack={() => setStep("disk")}
          onNext={(choice) => {
            setStorage(choice)
            setStep("features")
          }}
        />
      )}

      {step === "features" && storage && systemInfo && (
        <FeaturesScreen
          persona={persona}
          storage={storage}
          system={systemInfo}
          value={features}
          onBack={() => setStep("storage")}
          onNext={(choice) => {
            setFeatures(choice)
            setStep("config")
          }}
        />
      )}

      {step === "config" && (
        <UserConfigScreen
          value={system}
          buildConfig={buildConfig}
          onBack={() => setStep("features")}
          onNext={(choice) => {
            setSystem(choice)
            setStep("review")
          }}
        />
      )}

      {step === "review" && plan && config && (
        <ReviewScreen
          plan={plan}
          config={config}
          onBack={() => setStep("config")}
          onInstall={handleInstall}
        />
      )}

      {step === "progress" && <ProgressScreen onDone={handleDone} onError={handleError} />}

      {step === "done" && <DoneScreen warnings={warnings} />}

      {step === "error" && (
        <ErrorScreen message={error ?? "unknown error"} onRestart={() => setStep("review")} />
      )}
    </div>
  )
}

export default App
