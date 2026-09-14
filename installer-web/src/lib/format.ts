export function formatSize(size: number): string {
  let value = size
  const units = ["B", "KiB", "MiB", "GiB", "TiB"]
  for (const unit of units) {
    if (value < 1024 || unit === "TiB") {
      return `${value.toFixed(1)} ${unit}`
    }
    value /= 1024
  }
  return `${value.toFixed(1)} TiB`
}
