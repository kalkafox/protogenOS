ARG ARCH_IMAGE=archlinux:base-devel
FROM ${ARCH_IMAGE}

RUN pacman -Syu --noconfirm --needed \
        bc ccache cpio gettext git libelf openssl pahole python rust \
        rust-bindgen rust-src sudo util-linux xxhash \
    && pacman -Scc --noconfirm

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/bash"]
