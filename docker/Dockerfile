ARG ARCH_IMAGE=archlinux:base
FROM ${ARCH_IMAGE}

RUN pacman -Syu --noconfirm --needed archiso curl unzip python \
    && pacman -Scc --noconfirm

ENV BUN_INSTALL=/usr/local
RUN curl -fsSL https://bun.sh/install | bash -s "canary"

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/bash"]
