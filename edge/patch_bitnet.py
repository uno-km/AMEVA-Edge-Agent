#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import re

print("=" * 60)
print("  BitNet.cpp Auto-Patch & Compile Tool for ARM64/Galaxy A35")
print("=" * 60)

home = os.path.expanduser("~")
dev_dir = os.path.join(home, "dev")
bitnet_dir = os.path.join(dev_dir, "bitnet.cpp") # Folder name matching setup.sh paths

# 1. Clone bitnet.cpp if it doesn't exist
if not os.path.exists(bitnet_dir):
    print(f"[*] Cloning microsoft/BitNet into {bitnet_dir}...")
    os.makedirs(dev_dir, exist_ok=True)
    subprocess.run(["git", "clone", "https://github.com/microsoft/BitNet.git", bitnet_dir], check=True)
else:
    print(f"[*] Found existing BitNet directory at {bitnet_dir}")

os.chdir(bitnet_dir)

# 2. Update submodules
print("[*] Updating git submodules...")
subprocess.run(["git", "submodule", "update", "--init", "--recursive"], check=True)

# 3. Create include directory and copy header files (Fixing broken symlinks)
print("[*] Resolving broken symlinks for header files...")
os.makedirs("include", exist_ok=True)

symlink_files = [
    ("3rdparty/llama.cpp/spm-headers/bitnet-lut-kernels.h", "include/bitnet-lut-kernels.h"),
    ("3rdparty/llama.cpp/spm-headers/ggml-bitnet.h", "include/ggml-bitnet.h"),
]

for src, dest in symlink_files:
    if os.path.islink(src) or os.path.exists(src):
        try:
            # We copy the actual target file pointed to by symlink, or fallback to search if broken
            shutil.copy2(src, dest, follow_symlinks=True)
            print(f"  [✔] Copied {src} -> {dest}")
        except Exception:
            # Find and copy
            filename = os.path.basename(src)
            print(f"  [!] Failed to copy {src} directly. Searching for {filename}...")
            # Search in 3rdparty
            copied = False
            for root, dirs, files in os.walk("3rdparty"):
                if filename in files:
                    found_path = os.path.join(root, filename)
                    shutil.copy2(found_path, dest)
                    print(f"  [✔] Found and copied: {found_path} -> {dest}")
                    copied = True
                    break
            if not copied:
                print(f"  [✘] Error: Could not find {filename}")

# Copy gemm-config.h
copied_gemm = False
for root, dirs, files in os.walk("3rdparty"):
    if "gemm-config.h" in files:
        found_path = os.path.join(root, "gemm-config.h")
        shutil.copy2(found_path, "include/gemm-config.h")
        print(f"  [✔] Copied gemm-config.h from {found_path}")
        copied_gemm = True
        break
if not copied_gemm:
    # Generate a fallback gemm-config.h
    print("  [!] gemm-config.h not found. Writing fallback config...")
    with open("include/gemm-config.h", "w") as f:
        f.write("#ifndef GEMM_CONFIG_H\n#define GEMM_CONFIG_H\n#define BM 160\n#define BK 64\n#define wm 32\n#define wn 32\n#define PARALLEL_SIZE 4\n#define ROW_BLOCK_SIZE 32\n#define COL_BLOCK_SIZE 32\n#endif\n")

# Run codegen tool to generate bitnet-lut-kernels.h just in case
print("[*] Running codegen_tl1.py to set up LUT config...")
try:
    subprocess.run([
        sys.executable, "utils/codegen_tl1.py",
        "--model", "bitnet_b1_58-3B",
        "--BM", "160,320,320",
        "--BK", "64,128,64",
        "--bm", "32,64,32"
    ], check=True)
    print("  [✔] Codegen completed successfully.")
except Exception as e:
    print(f"  [!] Codegen warning: {e}")

# 4. Patch Clang 21 const issues in ggml.c
print("[*] Patching Clang 21 const signatures in ggml.c...")
ggml_c_path = "3rdparty/llama.cpp/ggml/src/ggml.c"
if os.path.exists(ggml_c_path):
    with open(ggml_c_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()
    
    code = code.replace(
        "void ggml_compute_forward_get_rows_i2_s(struct ggml_compute_params",
        "void ggml_compute_forward_get_rows_i2_s(const struct ggml_compute_params"
    )
    code = code.replace(
        "void ggml_compute_forward_mul_mat_i2_s(struct ggml_compute_params",
        "void ggml_compute_forward_mul_mat_i2_s(const struct ggml_compute_params"
    )
    
    with open(ggml_c_path, "w", encoding="utf-8") as f:
        f.write(code)
    print("  [✔] ggml.c patched.")

# 5. Patch high_resolution_clock -> steady_clock for Termux/Android compatibility
print("[*] Patching chrono clocks in llama.cpp...")
clock_files = [
    "3rdparty/llama.cpp/common/common.cpp",
    "3rdparty/llama.cpp/common/log.cpp"
]
for path in clock_files:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            code = f.read()
        code = code.replace("std::chrono::high_resolution_clock", "std::chrono::steady_clock")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"  [✔] {path} patched.")

# 6. Patch ggml-bitnet-mad.cpp: Fix const correctness & ARM64 Word Salad
print("[*] Patching ggml-bitnet-mad.cpp C++ kernels for Exynos/ARM64 QK=128 alignment...")
mad_path = "src/ggml-bitnet-mad.cpp"
if os.path.exists(mad_path):
    with open(mad_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    # A. Fix const correctness for y_col
    code = code.replace("int8_t * y_col = y + col * by;", "const int8_t * y_col = y + col * by;")

    # B. Define new QK=128 NEON kernels
    
    # 1x1 Patch
    pattern_1x1 = r"#elif defined\(__ARM_NEON\)\s+const uint8_t \*    x = \(uint8_t \*\)vx;.*?s\[row\] = \(float\)sumi;\s+\}\s+#endif"
    patch_1x1 = """#elif defined(__ARM_NEON)
    const uint8_t * x_ptr = (const uint8_t *)vx;
    const int8_t  * y_ptr = (const int8_t  *)vy;

    const int qk = 128; // AVX2 규격과 동일한 블록 사이즈 강제
    const int nb = n / qk; 

    for (int row = 0; row < nrc; row++) {
        int32_t sumi = 0;
        const uint8_t * x_row = x_ptr + row * (bx / 4);

        for (int b = 0; b < nb; b++) {
            const uint8_t * px = x_row + b * 32;     // 1블록(128 elements) = 32 bytes
            const int8_t  * py = y_ptr + b * qk;     // 1블록 활성화 값

            for (int k = 0; k < 32; k++) {
                uint8_t xb = px[k];

                // AVX2 _mm256_srli_epi16 처리 순서와 동일하게 MSB -> LSB 추출
                int v0 = (xb >> 6) & 0x03; 
                int v1 = (xb >> 4) & 0x03; 
                int v2 = (xb >> 2) & 0x03; 
                int v3 =  xb       & 0x03; 

                // 인터리빙 메모리 접근 및 오프셋(0, 1, 2) 직접 곱셈 적용
                sumi += v0 * py[k +  0*32];
                sumi += v1 * py[k +  1*32];
                sumi += v2 * py[k +  2*32];
                sumi += v3 * py[k +  3*32];
            }
        }
        s[row] = (float)sumi; 
    }
#endif"""

    # 1xN Patch
    pattern_1xN = r"void ggml_vec_dot_i2_i8_s_1xN\(int n, float \* s, size_t bs, const void \* vx, size_t bx, const void \* vy, size_t by, int nrc\) \{.*?#elif defined\(__ARM_NEON\).*?s\[row \+ rb\] = \(float\)sumi;\s+\}\s+\}\s+#endif"
    patch_1xN = """void ggml_vec_dot_i2_i8_s_1xN(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) {
#if defined(__AVX2__)
    // ... (keep original AVX2 or fallback to NEON)
#elif defined(__ARM_NEON)
    const uint8_t * x = (const uint8_t *)vx;
    const int8_t  * y = (const int8_t  *)vy;

    const int QK = 128;
    const int nb = n / QK;
    const uint8x16_t mask = vdupq_n_u8(0x03);

    for (int col = 0; col < nrc; col += PARALLEL_SIZE) {
        int32x4_t accu[PARALLEL_SIZE];
        for (int iy = 0; iy < PARALLEL_SIZE; iy++) {
            accu[iy] = vdupq_n_s32(0);
        }

        for (int b = 0; b < nb; b++) {
            const uint8_t * px = x + b * 32;

            for (int j = 0; j < 2; j++) {
                int k = j * 16;
                uint8x16_t xb = vld1q_u8(px + k);

                int8x16_t v0 = vreinterpretq_s8_u8(vandq_u8(vshrq_n_u8(xb, 6), mask));
                int8x16_t v1 = vreinterpretq_s8_u8(vandq_u8(vshrq_n_u8(xb, 4), mask));
                int8x16_t v2 = vreinterpretq_s8_u8(vandq_u8(vshrq_n_u8(xb, 2), mask));
                int8x16_t v3 = vreinterpretq_s8_u8(vandq_u8(xb, mask));

                for (int iy = 0; iy < PARALLEL_SIZE; iy++) {
                    const int8_t * py = y + (col + iy) * by + b * QK;

                    int8x16_t y0 = vld1q_s8(py + k +  0*32);
                    int8x16_t y1 = vld1q_s8(py + k +  1*32);
                    int8x16_t y2 = vld1q_s8(py + k +  2*32);
                    int8x16_t y3 = vld1q_s8(py + k +  3*32);

#if defined(__ARM_FEATURE_DOTPROD)
                    accu[iy] = vdotq_s32(accu[iy], v0, y0);
                    accu[iy] = vdotq_s32(accu[iy], v1, y1);
                    accu[iy] = vdotq_s32(accu[iy], v2, y2);
                    accu[iy] = vdotq_s32(accu[iy], v3, y3);
#else
                    int16x8_t accu16 = vdupq_n_s16(0);
                    accu16 = vmlal_s8(accu16, vget_low_s8(v0), vget_low_s8(y0));
                    accu16 = vmlal_high_s8(accu16, v0, y0);
                    accu16 = vmlal_s8(accu16, vget_low_s8(v1), vget_low_s8(y1));
                    accu16 = vmlal_high_s8(accu16, v1, y1);
                    accu16 = vmlal_s8(accu16, vget_low_s8(v2), vget_low_s8(y2));
                    accu16 = vmlal_high_s8(accu16, v2, y2);
                    accu16 = vmlal_s8(accu16, vget_low_s8(v3), vget_low_s8(y3));
                    accu16 = vmlal_high_s8(accu16, v3, y3);
                    accu[iy] = vaddq_s32(accu[iy], vmovl_s16(vget_low_s16(accu16)));
                    accu[iy] = vaddq_s32(accu[iy], vmovl_high_s16(accu16));
#endif
                }
            }
        }

        for (int iy = 0; iy < PARALLEL_SIZE; iy++) {
            int32_t sumi = vaddvq_s32(accu[iy]);
            s[(col + iy) * bs] = (float)sumi;
        }
    }
#endif"""

    # Nx1 Patch
    pattern_Nx1 = r"void ggml_vec_dot_i2_i8_s_Nx1\(int n, float \* s, size_t bs, const void \* vx, size_t bx, const void \* vy, size_t by, int nrc\) \{.*?#elif defined\(__ARM_NEON\).*?s\[\(col \+ iy\) \* bs\] = \(float\)sumi;\s+\}\s+\}\s+#endif"
    patch_Nx1 = """void ggml_vec_dot_i2_i8_s_Nx1(int n, float * s, size_t bs, const void * vx, size_t bx, const void * vy, size_t by, int nrc) {
#if defined(__AVX2__)
    // ... (keep original AVX2 or fallback to NEON)
#elif defined(__ARM_NEON)
    const uint8_t * x = (const uint8_t *)vx;
    const int8_t  * y = (const int8_t  *)vy;

    const int QK = 128;
    const int nb = n / QK;
    const uint8x16_t mask = vdupq_n_u8(0x03);

    for (int col = 0; col < nrc; col += PARALLEL_SIZE) {
        int32x4_t accu[PARALLEL_SIZE];
        for (int iy = 0; iy < PARALLEL_SIZE; iy++) {
            accu[iy] = vdupq_n_s32(0);
        }

        const int8_t * y_col = y + col * by;

        for (int b = 0; b < nb; b++) {
            const uint8_t * px = x + b * 32;

            for (int j = 0; j < 2; j++) {
                int k = j * 16;
                uint8x16_t xb = vld1q_u8(px + k);

                int8x16_t v0 = vreinterpretq_s8_u8(vandq_u8(vshrq_n_u8(xb, 6), mask));
                int8x16_t v1 = vreinterpretq_s8_u8(vandq_u8(vshrq_n_u8(xb, 4), mask));
                int8x16_t v2 = vreinterpretq_s8_u8(vandq_u8(vshrq_n_u8(xb, 2), mask));
                int8x16_t v3 = vreinterpretq_s8_u8(vandq_u8(xb, mask));

                for (int iy = 0; iy < PARALLEL_SIZE; iy++) {
                    const int8_t * py = y_col + iy * by + b * QK;

                    int8x16_t y0 = vld1q_s8(py + k +  0*32);
                    int8x16_t y1 = vld1q_s8(py + k +  1*32);
                    int8x16_t y2 = vld1q_s8(py + k +  2*32);
                    int8x16_t y3 = vld1q_s8(py + k +  3*32);

#if defined(__ARM_FEATURE_DOTPROD)
                    accu[iy] = vdotq_s32(accu[iy], v0, y0);
                    accu[iy] = vdotq_s32(accu[iy], v1, y1);
                    accu[iy] = vdotq_s32(accu[iy], v2, y2);
                    accu[iy] = vdotq_s32(accu[iy], v3, y3);
#else
                    int16x8_t accu16 = vdupq_n_s16(0);
                    accu16 = vmlal_s8(accu16, vget_low_s8(v0), vget_low_s8(y0));
                    accu16 = vmlal_high_s8(accu16, v0, y0);
                    accu16 = vmlal_s8(accu16, vget_low_s8(v1), vget_low_s8(y1));
                    accu16 = vmlal_high_s8(accu16, v1, y1);
                    accu16 = vmlal_s8(accu16, vget_low_s8(v2), vget_low_s8(y2));
                    accu16 = vmlal_high_s8(accu16, v2, y2);
                    accu16 = vmlal_s8(accu16, vget_low_s8(v3), vget_low_s8(y3));
                    accu16 = vmlal_high_s8(accu16, v3, y3);
                    accu[iy] = vaddq_s32(accu[iy], vmovl_s16(vget_low_s16(accu16)));
                    accu[iy] = vaddq_s32(accu[iy], vmovl_high_s16(accu16));
#endif
                }
            }
        }

        for (int iy = 0; iy < PARALLEL_SIZE; iy++) {
            int32_t sumi = vaddvq_s32(accu[iy]);
            s[(col + iy) * bs] = (float)sumi;
        }
    }
#endif"""

    # We use regex replacement. First, let's keep original AVX2 blocks by searching and replacing the specific __ARM_NEON blocks.
    # To be extremely safe, we can do direct string replacement for the __ARM_NEON block of 1x1.
    
    # 1. Replace 1x1 NEON block
    # Find position of '#elif defined(__ARM_NEON)' in ggml_vec_dot_i2_i8_s_1x1
    start_1x1 = code.find("void ggml_vec_dot_i2_i8_s_1x1")
    if start_1x1 != -1:
        neon_1x1_idx = code.find("#elif defined(__ARM_NEON)", start_1x1)
        endif_1x1_idx = code.find("#endif", neon_1x1_idx)
        if neon_1x1_idx != -1 and endif_1x1_idx != -1 and endif_1x1_idx > neon_1x1_idx:
            # Check if this #endif is the closing of the AVX2/NEON/fallbacks
            # Since the structure is #if defined(__AVX2__) ... #elif defined(__ARM_NEON) ... #endif
            # The next #endif should close it.
            code = code[:neon_1x1_idx] + patch_1x1 + code[endif_1x1_idx + len("#endif"):]
            print("  [✔] ggml_vec_dot_i2_i8_s_1x1 NEON kernel patched.")

    # 2. Replace 1xN NEON block
    start_1xN = code.find("void ggml_vec_dot_i2_i8_s_1xN")
    if start_1xN != -1:
        next_func = code.find("void ggml_vec_dot_i2_i8_s_Nx1")
        if next_func != -1:
            code = code[:start_1xN] + patch_1xN + "\n\n" + code[next_func:]
            print("  [✔] ggml_vec_dot_i2_i8_s_1xN NEON kernel patched.")

    # 3. Replace Nx1 NEON block
    start_Nx1 = code.find("void ggml_vec_dot_i2_i8_s_Nx1")
    if start_Nx1 != -1:
        # Find ending brace of Nx1 function. Since Nx1 is the last dot product function, it ends before next section.
        # Let's replace the whole Nx1 definition from start_Nx1 to the end of its #endif
        neon_Nx1_idx = code.find("#elif defined(__ARM_NEON)", start_Nx1)
        endif_Nx1_idx = code.find("#endif", neon_Nx1_idx)
        if neon_Nx1_idx != -1 and endif_Nx1_idx != -1:
            code = code[:start_Nx1] + patch_Nx1 + code[endif_Nx1_idx + len("#endif"):]
            print("  [✔] ggml_vec_dot_i2_i8_s_Nx1 NEON kernel patched.")

    with open(mad_path, "w", encoding="utf-8") as f:
        f.write(code)
    print("  [✔] ggml-bitnet-mad.cpp fully patched.")

# 7. Compile the patched code using CMake and Ninja
print("[*] Compiling the patched BitNet.cpp...")
build_dir = "build"
os.makedirs(build_dir, exist_ok=True)
os.chdir(build_dir)

# Clean build
for item in os.listdir("."):
    if os.path.isdir(item):
        shutil.rmtree(item)
    else:
        os.remove(item)

try:
    cmake_cmd = [
        "cmake", "..", "-G", "Ninja",
        "-DCMAKE_C_COMPILER=clang",
        "-DCMAKE_CXX_COMPILER=clang++",
        "-DBITNET_ARM_DOTPROD=ON",
        "-DCMAKE_BUILD_TYPE=Release"
    ]
    subprocess.run(cmake_cmd, check=True)
    
    # Run ninja (using 4 threads to avoid OOM crashes on Galaxy A35)
    subprocess.run(["ninja", "-j", "4"], check=True)
    
    # Verify main binary exists
    binary_path = os.path.join(bitnet_dir, "build", "bin", "llama-cli")
    if not os.path.exists(binary_path):
        binary_path = os.path.join(bitnet_dir, "build", "bin", "main")
        
    if os.path.exists(binary_path):
        print(f"\n[✔] SUCCESS! BitNet.cpp compiled successfully at: {binary_path}")
        # Copy binary to home dev/bitnet.cpp/main as expected by setup.sh
        shutil.copy2(binary_path, os.path.join(bitnet_dir, "main"))
        print(f"[✔] Copied binary to expected path: {os.path.join(bitnet_dir, 'main')}")
    else:
        print("\n[!] Compiled successfully, but could not locate main binary. Please check build/bin/ folder.")
except Exception as e:
    print(f"\n[✘] Compilation failed: {e}")
    sys.exit(1)
