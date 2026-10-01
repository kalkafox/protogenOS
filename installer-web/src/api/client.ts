import type {
  ApiErrorBody,
  DiskInfo,
  DiskLayout,
  KeyboardLayout,
  KeyboardState,
  MirrorCountry,
  InstallConfig,
  InstallPlan,
  InstallStatus,
  OptionGroup,
  NetworkStatus,
  Persona,
  PreflightCheck,
  ShrinkInfo,
  PrefetchStatus,
  SystemInfo,
  WifiNetwork,
} from "./types"

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  })
  const body = await response.json()
  if (!response.ok) {
    throw new ApiError((body as ApiErrorBody).error || `request failed: ${response.status}`)
  }
  return body as T
}

export function getPersonas(): Promise<{ personas: Persona[] }> {
  return request("/api/personas")
}

export function getOptions(persona: string): Promise<{ groups: OptionGroup[] }> {
  return request(`/api/options?persona=${encodeURIComponent(persona)}`)
}

export function resolvePlan(
  persona: string,
  selections: Record<string, string[]>
): Promise<InstallPlan> {
  return request("/api/plan/resolve", {
    method: "POST",
    body: JSON.stringify({ persona, selections }),
  })
}

export function getPreflight(): Promise<{ checks: PreflightCheck[] }> {
  return request("/api/preflight")
}

export function getPrefetch(): Promise<PrefetchStatus> {
  return request("/api/prefetch")
}

export function getSystem(): Promise<SystemInfo> {
  return request("/api/system")
}

export function rebootSystem(): Promise<{ rebooting: boolean }> {
  return request("/api/system/reboot", { method: "POST" })
}

export function getNetwork(): Promise<NetworkStatus> {
  return request("/api/network")
}

export function scanWifi(device: string): Promise<{ networks: WifiNetwork[] }> {
  return request("/api/network/scan", {
    method: "POST",
    body: JSON.stringify({ device }),
  })
}

export function connectWifi(
  device: string,
  ssid: string,
  passphrase: string
): Promise<{ connected: boolean; online: boolean }> {
  return request("/api/network/connect", {
    method: "POST",
    body: JSON.stringify({ device, ssid, passphrase }),
  })
}

export function getKeyboard(): Promise<KeyboardState> {
  return request("/api/keyboard")
}

export function getKeyboardLayouts(): Promise<{ layouts: KeyboardLayout[] }> {
  return request("/api/keyboard/layouts")
}

export function setKeyboard(layout: string, variant: string): Promise<KeyboardState> {
  return request("/api/keyboard", {
    method: "POST",
    body: JSON.stringify({ layout, variant }),
  })
}

export function getMirrorCountries(): Promise<{ countries: MirrorCountry[] }> {
  return request("/api/mirrors/countries")
}

export function getShrinkInfo(disk: string, partition: string): Promise<ShrinkInfo> {
  return request(
    `/api/disks/shrink?disk=${encodeURIComponent(disk)}&partition=${encodeURIComponent(partition)}`
  )
}

export function getDiskLayout(disk: string): Promise<DiskLayout> {
  return request(`/api/disks/layout?disk=${encodeURIComponent(disk)}`)
}

export function getDisks(): Promise<{ disks: DiskInfo[] }> {
  return request("/api/disks")
}

export function getTimezones(): Promise<{ timezones: string[] }> {
  return request("/api/timezones")
}

export function suggestLocale(timezone: string, layout: string): Promise<{ locale: string }> {
  const query = new URLSearchParams({ timezone, layout })
  return request(`/api/locales/suggest?${query}`)
}

// With a persona, settings that depend on it (such as Server SSH keys) are checked too.
export function validateConfig(
  config: Partial<InstallConfig>,
  persona?: string
): Promise<{ valid: boolean; error?: string }> {
  return request("/api/config/validate", {
    method: "POST",
    body: JSON.stringify(persona ? { ...config, persona } : config),
  })
}

export function getGithubKeys(username: string): Promise<{ keys: string[] }> {
  return request(`/api/ssh/github-keys?username=${encodeURIComponent(username)}`)
}

export function startInstall(
  plan: InstallPlan,
  config: InstallConfig,
  aurConfirmed: boolean
): Promise<{ started: boolean }> {
  return request("/api/install/start", {
    method: "POST",
    body: JSON.stringify({ plan, config, aur_confirmed: aurConfirmed }),
  })
}

export function getInstallStatus(since: number): Promise<InstallStatus> {
  return request(`/api/install/status?since=${since}`)
}

export function uploadInstallLog(): Promise<{ url: string }> {
  return request("/api/install/log/upload", { method: "POST" })
}
