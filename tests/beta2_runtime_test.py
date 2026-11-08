"""Differential models and C source invariants, not a native runtime compilation."""
import itertools
import random
import unittest
from pathlib import Path

from dirty_census_test import function
ROOT = Path(__file__).resolve().parent.parent


def pointer(updates, address, guest, same_fence=True, valid=True):
    if not valid:
        return 0, True
    if same_fence:
        for lo, data in reversed(updates[-4096:]):
            hi = lo + len(data)
            if address >= hi or address + 8 <= lo:
                continue
            if address < lo or address + 8 > hi:
                break
            return int.from_bytes(data[address-lo:address-lo+8], 'little'), True
    return guest, False


class Hits:
    def __init__(self, capacity):
        self.local = [None] * 16
        self.data = self.local
        self.n = 0
        self.capacity = capacity
        self.allocs = 0

    def append(self, value):
        if self.n == 16:
            self.data = [None] * self.capacity
            self.data[:16] = self.local
            self.allocs += 1
        self.data[self.n] = value
        self.n += 1

    def values(self):
        return self.data[:self.n]


def old_candidates(entries, fence, trace):
    count = sum(e['dirty'] and e['wanted'] for e in entries)
    hits = [None] * count
    used = 0
    later = False
    notes = []
    for i, e in enumerate(entries):
        if not e['dirty']:
            continue
        if not e['wanted']:
            if trace and e['fence'] <= fence:
                notes.append((i, 'skip'))
            continue
        if e['fence'] > fence:
            later = True
        else:
            if trace and e['large']:
                notes.append((i, 'large'))
            hits[used] = i
            used += 1
    return hits[:used], later, notes


def new_candidates(entries, fence, trace):
    hits = Hits(len(entries))
    later = False
    notes = []
    for i, e in enumerate(entries):
        if not e['dirty']:
            continue
        if not e['wanted']:
            if trace and e['fence'] <= fence:
                notes.append((i, 'skip'))
            continue
        if e['fence'] > fence:
            later = True
        else:
            if trace and e['large']:
                notes.append((i, 'large'))
            hits.append(i)
    return hits.values(), later, notes


class RuntimeTest(unittest.TestCase):
    def test_pointer_reads_and_concurrent_guest_changes(self):
        full = (100, bytes(range(16)))
        for updates in ([], [full], [full, (102, b'X')], [(99, bytes(range(9)))],
                        [full] + [(200, b'Y')] * 4096, [(200, b'Y')] * 4096 + [full]):
            for address, same, valid in itertools.product((99, 100, 101, 108, 109, 200), (False, True), (False, True)):
                first, stable = pointer(updates, address, 11, same, valid)
                original_second, _ = pointer(updates, address, 22, same, valid)
                second = first if stable else pointer(updates, address, 22, same, valid)[0]
                self.assertEqual(original_second, second)
                if stable:
                    self.assertEqual(first, second)
                elif not updates or not same:
                    self.assertEqual((first, second), (11, 22))
        # Updated bytes change between draws: the local result is never retained.
        self.assertNotEqual(pointer([full], 100, 0)[0], pointer([(100, b'Z'*16)], 100, 0)[0])

    def test_hit_storage_bounds_and_order(self):
        for total in (0, 1, 15, 16, 17, 31, 32, 65, 1024):
            for count in sorted({0, min(16, total), min(17, total), total}):
                hits = Hits(total)
                for i in range(count):
                    hits.append(i)
                self.assertEqual(hits.values(), list(range(count)))
                self.assertEqual(hits.allocs, int(count > 16))
                self.assertLessEqual(hits.n, len(hits.data))
        # At signed-int maximum nowned, size_t multiplication remains representable on x86-64.
        self.assertLess((2**31-1)*8, 2**64)

    def test_fence_candidates_logs_and_order(self):
        rng = random.Random(121)
        for _ in range(5000):
            entries = [dict(dirty=rng.randrange(2), wanted=rng.randrange(2), large=rng.randrange(2),
                            fence=rng.randrange(4), seq=rng.randrange(8), rewritten=rng.randrange(2))
                       for i in range(rng.choice((0, 1, 16, 17, 32, 70)))]
            fence, trace = rng.randrange(4), rng.randrange(2)
            old = old_candidates(entries, fence, trace)
            new = new_candidates(entries, fence, trace)
            self.assertEqual(old, new)
            def finish(candidates):
                survivors = [i for i in candidates if not entries[i]['rewritten']]
                return sorted(survivors, key=lambda i: entries[i]['seq']), len(candidates)-len(survivors)
            self.assertEqual(finish(old[0]), finish(new[0]))

    def test_c_source_invariants(self):
        draw = (ROOT/'runtime/vk_exec_draw.c').read_text(encoding='utf-8')
        texture = (ROOT/'runtime/vk_texture.c').read_text(encoding='utf-8')
        ubo = function(draw, 'ubo_ptr')
        self.assertLess(ubo.index('if (stable) *stable = 1;'), ubo.index('upd_read('))
        self.assertLess(ubo.index('upd_read('), ubo.index('if (stable) *stable = 0;'))
        self.assertLess(ubo.index('if (stable) *stable = 0;'), ubo.index('vk_mem_resolve('))
        sig = function(draw, 'bind_sig')
        self.assertIn('uint64_t ptr[RS_PTRS];', sig)
        self.assertIn('stable[i] ? ptr[i] : ubo_ptr(ex, l->p[i].st, l->p[i].res, NULL)', sig)
        self.assertIn('if (np == RS_PTRS) { ok = 0; break; }', function(draw, 'rs_list'))
        small = function(texture, 'vk_texture_write_back_small')
        self.assertEqual(small.count('for (int i = 0; i < nowned; i++)'), 1)
        self.assertIn('plat_mutex_lock_shared(&owned_lock);', small)
        self.assertIn('plat_mutex_unlock_shared(&owned_lock);', small)
        self.assertIn('if (later) __atomic_store_n(&small_pending, 1, __ATOMIC_RELEASE);', small)
        for name in ('vk_texture_write_back', 'vk_texture_write_back_small'):
            body = function(texture, name)
            self.assertIn('if (hit != hits.local) free(hit);', body)
            self.assertIn('write_back_n(hit, k);', body)
            self.assertIn('hit[j - 1]->gpu_seq > hit[j]->gpu_seq', body)
            self.assertIn('storage_rewritten(hit[i], hit[i]->gpu_gen, &now)', body)
        helper = function(texture, 'wb_hit_add')
        self.assertIn('if (hits->n == WB_LOCAL_HITS)', helper)
        self.assertIn('malloc(sizeof *p * (size_t)nowned)', helper)
        self.assertIn('memcpy(p, hits->local, sizeof hits->local);', helper)


if __name__ == '__main__':
    unittest.main(verbosity=2)
