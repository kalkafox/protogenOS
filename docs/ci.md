# Self-hosted kernel runner

The kernel workflow runs on an x86-64 Linux runner carrying the custom label
`protogenos-build`. Its five build jobs run serially. The ISO workflow uses
GitHub's standard `ubuntu-latest` runner and does not use this machine.

## Host requirements

- x86-64 Linux with at least 16 GB RAM; 32 GB or more is recommended.
- At least 100 GB of free SSD space for Docker layers, kernel sources, and
  artifacts.
- Docker Engine with the Buildx plugin.
- A dedicated, unprivileged runner account allowed to use Docker.
- Reliable network access to GitHub, Arch repositories, and kernel.org.

In the repository, open **Settings > Actions > Runners > New self-hosted
runner** and follow GitHub's generated installation commands as the dedicated
account. During configuration, add the project label:

```bash
./config.sh --url https://github.com/OWNER/protogenos \
  --token ONE_TIME_TOKEN --labels protogenos-build --unattended
sudo ./svc.sh install
sudo ./svc.sh start
```

Use the exact repository URL and temporary token shown by GitHub. Confirm that
the runner appears online with the automatic `self-hosted`, `linux`, and `x64`
labels plus `protogenos-build`. Verify Docker access from the runner account:

```bash
docker version
docker buildx version
```

## Security and maintenance

Do not enable the kernel workflow for pull requests from forks. A workflow
running on a self-hosted machine can access its Docker daemon and host
resources. The kernel workflow runs only through maintainer dispatches. Keep
the runner application, host packages, and Docker updated, monitor free disk
space, and periodically prune unused project builder images.

GitHub currently does not charge execution minutes for self-hosted runners, but
uploaded artifacts can consume billable Actions storage. Both workflows retain
artifacts for 14 days to limit storage accumulation.
