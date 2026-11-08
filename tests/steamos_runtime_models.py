"""Differential models for the SteamOS runtime changes, without game data or Vulkan.

Run with Python 3.12+: python tests/steamos_runtime_models.py
These are algorithm/source checks, not execution or compilation of the C runtime.
"""
from array import array
from itertools import product
from pathlib import Path
import random
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
MASK32 = (1 << 32) - 1
MASK64 = (1 << 64) - 1


def barriers(ranges, bounded):
    since, lo, hi, comparisons = 0, MASK64, 0, 0
    result = []
    for i, (dst, size) in enumerate(ranges):
        end = (dst + size) & MASK64
        if not bounded or (dst < hi and lo < end):
            for k in range(since, i):
                a, n = ranges[k]
                comparisons += 1
                if dst < ((a + n) & MASK64) and a < end:
                    result.append(i)
                    since, lo, hi = i, MASK64, 0
                    break
        lo, hi = min(lo, dst), max(hi, end)
    return result, comparisons


def deduplicate(ranges, last_first):
    written, comparisons = [], 0
    for current in ranges:
        order = list(range(len(written)))
        if last_first and order:
            order = order[-1:] + order[:-1]
        for i in order:
            comparisons += 1
            if written[i] == current:
                break
        else:
            written.append(current)
    return written, comparisons


class RuntimeModels(unittest.TestCase):
    def test_line_loop(self):
        rng = random.Random(313)
        cases = [(f, n) for f in (0, MASK32, 1 << 31) for n in (2, 3, 255, 256)]
        cases += [(rng.randrange(1 << 32), rng.randrange(2, 1000)) for _ in range(100)]
        cases += [(MASK32 - 2, n) for n in ((1 << 20) - 2, (1 << 20) - 1, 1 << 20)]
        for first, n in cases:
            temporary = array('I', ((first + i) & MASK32 for i in range(n)))
            temporary.append(first)
            original = temporary.tobytes()
            direct = array('I', [0xDEADBEEF]) * (n + 3)
            for i in range(n):
                direct[i + 1] = (first + i) & MASK32
            direct[n + 1] = first
            self.assertEqual(direct[1:-1].tobytes(), original)
            self.assertEqual((direct[0], direct[-1]), (0xDEADBEEF, 0xDEADBEEF))
        n = (1 << 31) - 1
        self.assertEqual(4 * (n + 1), 1 << 33)
        self.assertLess(n + 1, 1 << 32)

    def test_byte_indices(self):
        for restart in (False, True):
            for count in (1, 255, 256, 257, (1 << 21) - 1, 1 << 21, (1 << 21) + 1):
                src = (bytes(range(256)) * ((count + 255) // 256))[:count]
                temporary = array('H', (0xFFFF if restart and v == 255 else v for v in src))
                direct = array('H', [0xABCD]) * (count + 2)
                for i, v in enumerate(src):
                    direct[i + 1] = 0xFFFF if restart and v == 255 else v
                self.assertEqual(direct[1:-1].tobytes(), temporary.tobytes())
                self.assertEqual((direct[0], direct[-1]), (0xABCD, 0xABCD))
        self.assertEqual(2 * ((1 << 31) - 1), 4294967294)

    def test_barrier_positions(self):
        # Empty, adjacent, contained, repeated, overlapping, and wrapped intervals.
        choices = [(0, 0), (0, 1), (1, 1), (0, 4), (2, 1), (3, 3), (MASK64, 2)]
        for seq in product(choices, repeat=4):
            self.assertEqual(barriers(seq, False)[0], barriers(seq, True)[0])
        rng = random.Random(813)
        for _ in range(10000):
            seq = [(rng.choice((rng.randrange(128), MASK64 - rng.randrange(128))),
                    rng.randrange(256)) for _ in range(rng.randrange(40))]
            self.assertEqual(barriers(seq, False)[0], barriers(seq, True)[0])
        seq = [(i * 16, 16) for i in range(1000)]
        self.assertEqual(barriers(seq, False), ([], 499500))
        self.assertEqual(barriers(seq, True), ([], 0))

    def test_exact_range_deduplication(self):
        choices = [(0, 0), (0, 1), (0, 2), (1, 1), (MASK64, 1)]
        for seq in product(choices, repeat=6):
            self.assertEqual(deduplicate(seq, False)[0], deduplicate(seq, True)[0])
        seq = [(i, 4) for i in range(1000)] + [(999, 4)] * 1000
        old, a = deduplicate(seq, False)
        new, b = deduplicate(seq, True)
        self.assertEqual(old, new)
        self.assertEqual(a - b, 999000)

    def test_upload_region_storage(self):
        # CPU upload metadata is host-only, zeroed, retained through the whole call.
        for n, agpu in product((0, 1, 15, 16, 17, 256, 4096), (False, True)):
            old_rg = [0] * n
            local_rg = [0] * 16
            new_rg = local_rg if n <= 16 else [0] * n
            old_ar = [0] * n if agpu else None
            local_ar = [0] * 16
            new_ar = (local_ar if n <= 16 else [0] * n) if agpu else None
            for k in range(n):
                old_rg[k] = new_rg[k] = (k, k * 16)
                if agpu:
                    old_ar[k] = new_ar[k] = (k, k + 1)
            self.assertEqual(old_rg, new_rg[:n])
            self.assertEqual(old_ar, None if new_ar is None else new_ar[:n])
            self.assertEqual(new_rg is local_rg, n <= 16)
            self.assertEqual(new_ar is local_ar, agpu and n <= 16)

    def test_source_invariants(self):
        draw = (ROOT / 'runtime/vk_exec_draw.c').read_text()
        base = subprocess.check_output(
            ['git', 'show', 'b8b948e:runtime/vk_exec_draw.c'], cwd=ROOT, text=True)
        begin = '    if (d->prim == NVN_DRAW_PRIMITIVE_QUADS && n >= 4)'
        end = '    } else if (d->prim == NVN_DRAW_PRIMITIVE_LINE_LOOP'
        self.assertEqual(draw[draw.index(begin):draw.index(end)],
                         base[base.index(begin):base.index(end)])
        for loop in ('for (uint32_t i = 0; i < n; i++) ix[i] = first + i;',
                     'ix[n] = first;', 'ix[i] = restart && v == 0xff ? 0xffff : v;'):
            self.assertIn(loop, draw)
            self.assertIn(loop, base)
        self.assertNotIn('bind_index(', draw)
        self.assertIn('vk_completion_add(ex->fence, cb_done, cur);', draw)
        self.assertIn('lo = UINT64_MAX, hi = 0;', draw)
        texture = (ROOT / 'runtime/vk_texture.c').read_text()
        self.assertIn('if (ar != local_ar) free(ar);', texture)
        self.assertIn('if (rg != local_rg) free(rg);', texture)
        self.assertIn('if (rg == local_rg) memset(rg, 0, n * sizeof *rg);', texture)
        self.assertIn('if (ar == local_ar) memset(ar, 0, n * sizeof *ar);', texture)
        astc = (ROOT / 'runtime/vk_astc.c').read_text()
        self.assertIn('if (copies != local_copies) free(copies);', astc)
        self.assertIn('if (bcp != local_bcp) free(bcp);', astc)

    def test_astc_chunk_metadata(self):
        # The same local arrays are overwritten for each chunk. Only its count is submitted.
        for n in (1, 15, 16, 17, 128):
            for chunk in (1, 3, 16, 32):
                reference = []
                actual = []
                old = [0] * n
                new = [0] * (16 if n <= 16 else n)
                for first in range(0, n, chunk):
                    count = min(chunk, n - first)
                    for i in range(count):
                        old[i] = new[i] = (first + i, 16 * i, 32 * i)
                    reference.append(old[:count])
                    actual.append(new[:count])
                self.assertEqual(reference, actual)


if __name__ == '__main__':
    unittest.main(verbosity=2)
