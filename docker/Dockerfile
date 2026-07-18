ARG ARCH_IMAGE=archlinux:base
FROM ${ARCH_IMAGE}

RUN pacman -Syu --noconfirm --needed archiso \
    && pacman -Scc --noconfirm

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/bash"]
