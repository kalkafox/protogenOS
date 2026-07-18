# protogenOS kernel flavors

These files are small overrides for the pinned upstream linux-tkg build system.
Every build produces a kernel and matching headers package. Distributed builds
target `x86-64`, never the build machine's native CPU.

| Flavor | Intended use | Persona recommendation |
| --- | --- | --- |
| `generic` | Conservative linux-tkg desktop configuration | General |
| `performance` | BORE scheduler and performance governor | Gamer |
| `developer` | Unstripped debug symbols and tracing | Developer, opt-in |
| `zen` | EEVDF plus linux-tkg's Zen/Liquorix-oriented Zenify patches | General or Gamer, opt-in |
| `minimal-vm` | linux-tkg diet build for controlled VMs only | Expert, experimental |

Personas describe application workloads, while kernel flavors describe runtime
and support tradeoffs. They are recommendations, not forced pairings. General
and Developer installations should default to the supported stock kernel at
first; Gamer may recommend Performance after the custom repository is live.

The ISO must retain Arch's `linux` kernel as a recovery option. Do not ship
`minimal-vm` on physical hardware: linux-tkg warns that diet builds can omit a
needed module. `zen` is a linux-tkg flavor using its Zenify patch selection; it
does not rebuild or repackage Arch's separate `linux-zen` package.

Build with `./scripts/build-kernel FLAVOR`. The Docker build clones the exact
commit in `linux-tkg.lock`, verifies the checkout, applies the selected external
configuration, and writes packages plus `SHA256SUMS` to
`kernels/out/FLAVOR/`. Expect at least 25 GB of free space for one full build.
The manual GitHub Actions matrix intentionally targets the shared self-hosted
runner label `protogenos-build`; standard hosted runners do not provide a
comfortable disk or time margin for five kernel builds. See `docs/ci.md` for
runner requirements and registration.

Before publishing a flavor:

1. Verify the package checksum and preserve its matching headers package.
2. Boot it under UEFI QEMU and test networking, storage, graphics, and audio.
3. Boot the fallback kernel from the same installed system.
4. For Zen and Performance, test latency-sensitive desktop and gaming workloads.
