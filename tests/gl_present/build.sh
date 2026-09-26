#!/usr/bin/env bash
# Builds the GL-PRESENT-01 tools into ./build with the exact flags the freeze doc hashes.
# Vulkan headers come from the Mesa 26.0.6 source tree used for /opt/mesa-kgsl (Ubuntu has no libvulkan-dev here).
set -euo pipefail
cd "$(dirname "$0")"
MESA_INC=/root/build/mesa-kgsl/mesa-26.0.6/include
mkdir -p build
cc -O2 -Wall -shared -fPIC -o build/present_count.so present_count.c -Wl,--no-as-needed \
   -lxcb-dri3 -lxcb-present -lxcb-shm -lxcb -lXext -lX11 -ldl
cc -O2 -Wall -I"$MESA_INC" -o build/vk_present vk_present.c -l:libvulkan.so.1 -lxcb
cc -O2 -Wall -DAPI_EGL -o build/gl_present_egl gl_present.c -lEGL -lGLESv2 -lX11
cc -O2 -Wall -DAPI_GLX -o build/gl_present_glx gl_present.c -lGL -lX11
# amendment 1: protocol-level request histogram and root-pixel probe (X-side witnesses)
cc -O2 -Wall -shared -fPIC -o build/req_count.so req_count.c -Wl,--no-as-needed -lxcb -ldl
cc -O2 -Wall -o build/px_probe px_probe.c -lxcb
(cd build && sha256sum present_count.so vk_present gl_present_egl gl_present_glx req_count.so px_probe)
