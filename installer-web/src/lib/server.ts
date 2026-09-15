import type { ServerChoice } from "@/api/types"

export const EMPTY_SERVER: ServerChoice = {
  ssh_authorized_keys: [],
  static_address: "",
  static_gateway: "",
  static_dns: [],
  static_interface: "",
}

// Mirrors SSH_KEY_PATTERN in server.py so paste mistakes show up right away.
export const SSH_KEY_PATTERN =
  /^(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/]+={0,3}(?: .*)?$/

export function describeNetwork(server: ServerChoice): string {
  if (!server.static_address) return "DHCP"
  return [
    `Static ${server.static_address}`,
    server.static_gateway && `via ${server.static_gateway}`,
    server.static_dns.length > 0 && `DNS ${server.static_dns.join(", ")}`,
    server.static_interface && `on ${server.static_interface}`,
  ]
    .filter(Boolean)
    .join(" ")
}
