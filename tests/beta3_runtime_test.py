"""Beta 3 differential models and source invariants; these do not compile C."""
import random
import unittest
from pathlib import Path
from dirty_census_test import function

ROOT = Path(__file__).resolve().parent.parent


def original_offsets(ranges, lo, ssbo, hi, width):
    result = []
    for off, length in ranges:
        if off + length <= lo or off >= hi:
            continue
        for o in range(0, length - width + 1, width):
            at = off + o
            if at < lo or at + width > hi:
                continue
            if (at - (lo if at < ssbo else ssbo)) % width:
                continue
            result.append(at)
    return result


def reference_signature(ranges, memory, lo, ssbo, hi, width):
    # Independently scan every complete element in each range, retaining duplicates and order.
    reads = []
    for off, length in ranges:
        for at in range(off, off + length - width + 1, width):
            if lo <= at and at + width <= hi and (at - (lo if at < ssbo else ssbo)) % width == 0:
                if memory.get(at, 0):
                    reads.append((at, memory[at]))
    return reads


class Views:
    def __init__(self, fast):
        self.fast = fast
        self.entries = []
        self.last = 0
        self.created = 0

    def get(self, key):
        if self.fast and self.entries and self.entries[self.last][0] == key:
            return self.entries[self.last][1]
        # Preserve the original fallback identity (blocks only gates the new fast path).
        for i, (old, handle) in enumerate(self.entries):
            if old[:7] == key[:7]:
                self.last = i
                return handle
        self.created += 1
        self.last = len(self.entries)
        self.entries.append((key, self.created))
        return self.created

    def finish(self):
        freed = [handle for key, handle in self.entries]
        self.entries = []
        self.last = 0
        return freed


class Beta3Tests(unittest.TestCase):
    def test_overlay_offsets_and_mutable_values(self):
        rng = random.Random(30581)
        for width in (16, 24, 32):
            lo, ssbo, hi = 64, 64 + width * 108, 64 + width * 204
            cases = [[], [(lo, width)], [(lo, hi-lo)], [(lo, width)] * 1024,
                     [(lo-1, width+1)], [(hi-width, width)], [(hi, width)],
                     [(ssbo-width, 2*width)], [(0, hi+width)]]
            cases += [[(rng.randrange(hi+64), rng.randrange(width*20)) for _ in range(30)] for _ in range(1000)]
            for ranges in cases:
                offsets = original_offsets(ranges, lo, ssbo, hi, width)
                self.assertLessEqual(len(offsets), sum(n for _, n in ranges)//width)
                for _ in range(2):  # Changes between draws must be observed without rebuilding metadata.
                    memory = {at: rng.randrange(4) for at in range(lo, hi, width)}
                    actual = [(at, memory[at]) for at in offsets if memory.get(at, 0)]
                    self.assertEqual(actual, reference_signature(ranges, memory, lo, ssbo, hi, width))

    def test_view_keys_growth_and_submission_lifetime(self):
        old, new = Views(False), Views(True)
        rng = random.Random(8)
        base = (1, 2, 3, 4, 5, 6, 7, 0)
        keys = [base]
        for field in range(8):
            key = list(base)
            key[field] += 1
            keys.append(tuple(key))
        keys += [(i, 2, 3, 4, 5, 6, 7, i % 2) for i in range(100)]
        for _ in range(10):
            for key in keys + [rng.choice(keys) for _ in range(1000)]:
                for _ in range(3):
                    self.assertEqual(old.get(key), new.get(key))
            self.assertEqual(old.finish(), new.finish())

    def test_c_source_contracts(self):
        draw = (ROOT/'runtime/vk_exec_draw.c').read_text()
        sig = function(draw, 'bind_sig')
        build = function(draw, 'rs_list')
        self.assertNotIn('offsetof(VkExec, ubo)', sig)
        self.assertIn('offsets[l->nov++] = (uint32_t)at;', build)
        self.assertIn('offset_cap * sizeof(uint32_t)', build)
        self.assertIn('stable[i] ? ptr[i] : ubo_ptr', sig)
        self.assertIn('ov_at(ex, b->addr, b->size ? b->size : ov_rest(b->addr))', sig)
        source = (ROOT/'runtime/vk_exec.c').read_text()
        view = function(source, 'view_of')
        self.assertLess(view.index('vk_exec_target_view'), view.index('ex->last_view'))
        self.assertIn('v[ex->last_view].blocks == !!t->blocks', view)
        self.assertIn('ex->last_view = ex->nviews;', view)
        self.assertEqual(source.count('ex->nviews = 0, ex->last_view = 0;'), 2)
        self.assertIn('vk_completion_add(fence, free_views, l);', source)


if __name__ == '__main__':
    unittest.main()
