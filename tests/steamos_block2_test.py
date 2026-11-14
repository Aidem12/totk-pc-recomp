"""Focused native C differential checks; no game data, Vulkan device or full build needed."""
import ctypes
import hashlib
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function(path, signature):
    source = (ROOT / path).read_text()
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class Block2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which("clang")
        if not cc:
            raise unittest.SkipTest("Native Clang and Linux Vulkan headers required")
        cls.temp = tempfile.TemporaryDirectory(prefix="totk-block2-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "checks.c"
        prelude = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <vulkan/vulkan.h>
#include "sha256.h"
#define CONTROL_SIZE 0x880
#define A8(n) (((n) + 7) & ~(size_t)7)
#define OWNED(cb) ((cb) != VK_NULL_HANDLE)
#define FALLBACK() (++fallbacks)
#define R_BeginRendering 1
static uint64_t packet[4096];
static size_t reserved_bytes;
static int publications, fallbacks, direct_calls;
static void *put(int op, size_t bytes) {
    if (op != R_BeginRendering || bytes > sizeof packet) abort();
    reserved_bytes = bytes;
    memset(packet, 0xa5, sizeof packet);
    return packet;
}
static void *tail_copy(uint8_t **at, const void *p, size_t bytes) {
    if (!p || !bytes) return NULL;
    void *result = *at;
    memcpy(result, p, bytes);
    *at += A8(bytes);
    return result;
}
static void pub(void) { publications++; }
void vkCmdBeginRendering(VkCommandBuffer cb, const VkRenderingInfo *ri) { direct_calls++; }
typedef struct { VkCommandBuffer cb; uint64_t fence; } CmdSlot;
typedef struct { VkDevice device; VkSemaphore timeline; } VkCtx;
static CmdSlot *slots;
static int nslots, cap_slots, grows, drains, allocations;
static uint64_t gpu_value;
static VkCommandPool cmd_pool;
static void rec_drain(void) { drains++; }
static void rt_fatal(const char *s) { abort(); }
static void *count_realloc(void *p, size_t bytes) { grows++; return realloc(p, bytes); }
VkResult vkGetSemaphoreCounterValue(VkDevice d, VkSemaphore s, uint64_t *value) {
    *value = gpu_value; return VK_SUCCESS;
}
VkResult vkAllocateCommandBuffers(VkDevice d, const VkCommandBufferAllocateInfo *ai, VkCommandBuffer *cb) {
    if (ai->commandBufferCount != 1 || ai->commandPool != cmd_pool) abort();
    *cb = (VkCommandBuffer)(uintptr_t)++allocations;
    return VK_SUCCESS;
}
"""
        code = prelude + function("runtime/gfx_shader.c", "static void build_code_hash(")
        code += function("runtime/vk_rec.c", "void rc_vkCmdBeginRendering(")
        code += "\n#define realloc count_realloc\n"
        code += function("runtime/vk_sync.c", "static int claim_slot(")
        code += r"""
#undef realloc
void test_hash(int st, const uint8_t *control, const uint8_t *code, uint64_t n,
               const uint8_t *c1, uint32_t nc, uint8_t *out) {
    build_code_hash(st, control, code, n, c1, nc, out);
}
int test_packets(void) {
    VkRenderingAttachmentInfo a[10];
    memset(a, 0, sizeof a);
    for (int i = 0; i < 10; i++) {
        a[i].sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
        a[i].imageView = (VkImageView)(uintptr_t)(i + 1);
        a[i].loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
        a[i].clearValue.color.uint32[0] = i;
    }
    for (unsigned colors = 0; colors <= 8; colors++)
    for (int mask = 0; mask < 4; mask++)
    for (int shared = 0; shared < 2; shared++) {
        VkRenderingInfo ri = {.sType = VK_STRUCTURE_TYPE_RENDERING_INFO, .layerCount = 1,
            .colorAttachmentCount = colors, .pColorAttachments = a,
            .pDepthAttachment = mask & 1 ? &a[8] : NULL,
            .pStencilAttachment = mask & 2 ? &a[shared ? 8 : 9] : NULL};
        publications = fallbacks = direct_calls = 0;
        rc_vkCmdBeginRendering((VkCommandBuffer)(uintptr_t)1, &ri);
        if (publications != 1 || fallbacks || direct_calls) return 1;
        VkRenderingInfo *out = (void *)packet;
        size_t expected = sizeof ri + A8(colors * sizeof *a) +
            ((!!(mask & 1)) + (!!(mask & 2))) * A8(sizeof *a);
        if (reserved_bytes != expected) return 2;
        if (colors && memcmp(out->pColorAttachments, a, colors * sizeof *a)) return 3;
        if (ri.pDepthAttachment && memcmp(out->pDepthAttachment, ri.pDepthAttachment, sizeof *a)) return 4;
        if (ri.pStencilAttachment && memcmp(out->pStencilAttachment, ri.pStencilAttachment, sizeof *a)) return 5;
        if (!ri.pDepthAttachment && out->pDepthAttachment) return 6;
        if (!ri.pStencilAttachment && out->pStencilAttachment) return 7;
        VkRenderingInfo restored = *out;
        restored.pColorAttachments = ri.pColorAttachments;
        restored.pDepthAttachment = ri.pDepthAttachment;
        restored.pStencilAttachment = ri.pStencilAttachment;
        if (memcmp(&restored, &ri, sizeof ri)) return 8;
        for (size_t i = expected; i < sizeof packet; i++)
            if (((uint8_t *)packet)[i] != 0xa5) return 9;
        // A chain still takes the synchronous fallback, as does an unowned buffer.
        ri.pNext = &a[0];
        rc_vkCmdBeginRendering((VkCommandBuffer)(uintptr_t)1, &ri);
        if (fallbacks != 1 || direct_calls != 1 || publications != 1) return 10;
        rc_vkCmdBeginRendering(VK_NULL_HANDLE, &ri);
        if (fallbacks != 1 || direct_calls != 2 || publications != 1) return 11;
    }
    return 0;
}
int test_slots(void) {
    uint64_t original[1024] = {0};
    int count = 0;
    VkCtx c = {0};
    grows = drains = allocations = 0;
    for (unsigned i = 1; i <= 1024; i++) {
        gpu_value = i < 600 ? 0 : i - 580;
        int expected = 0;
        while (expected < count && original[expected] > gpu_value) expected++;
        if (expected == count) count++;
        original[expected] = i;
        int actual = claim_slot(&c, i);
        if (actual != expected || nslots != count) return 1;
        for (int j = 0; j < count; j++)
            if (slots[j].fence != original[j] || slots[j].cb != (VkCommandBuffer)(uintptr_t)(j + 1)) return 2;
    }
    if (allocations != count || drains != count || grows != 8 || cap_slots != 1024) return 3;
    free(slots); slots = NULL; nslots = cap_slots = 0;
    return 0;
}
"""
        source.write_text(code)
        lib = Path(cls.temp.name) / "checks.so"
        subprocess.run([cc, "-shared", "-fPIC", "-O2", "-I", str(ROOT / "runtime"),
                        str(source), str(ROOT / "runtime/sha256.c"), "-o", str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.test_hash.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]

    def test_shader_hash_matches_original_layout(self):
        rng = random.Random(42)
        control = rng.randbytes(0x880)
        # SHA block/padding boundaries and a large shader; absent and independent constants.
        sizes = [0, 1, 3, 4, 31, 55, 56, 63, 64, 65, 127, 128, 4096, 1024 * 1024]
        for size in sizes:
            code = rng.randbytes(size)
            for nc in (0, 1, 55, 64, 65, 257):
                constants = rng.randbytes(nc)
                for stage in (0, 1, 5):
                    expected = hashlib.sha256(struct.pack("<I", stage) + control +
                        struct.pack("<Q", size) + code + struct.pack("<I", nc) + constants).digest()
                    out = ctypes.create_string_buffer(32)
                    self.lib.test_hash(stage, control, code if size else None, size,
                                       constants if nc else None, nc, out)
                    self.assertEqual(out.raw, expected, (size, nc, stage))

    def test_recorder_attachment_payload(self):
        self.assertEqual(self.lib.test_packets(), 0)

    def test_slot_reuse_and_growth(self):
        self.assertEqual(self.lib.test_slots(), 0)
        self.assertEqual(self.lib.test_slots(), 0)  # Shutdown/reinitialization.


if __name__ == "__main__":
    unittest.main()
