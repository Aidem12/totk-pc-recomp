"""Differential models for block 1; not GPU/runtime execution tests."""
import random
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def copies(lo, hi, pending, first=0):
    for i in range(first, len(pending)):
        a, b = pending[i]
        if a < hi and b > lo:
            return (copies(lo, a, pending, i+1) if a > lo else []) + (copies(b, hi, pending, i+1) if b < hi else [])
    return [(lo, hi)]

class BlockTests(unittest.TestCase):
    def test_shadow_runs_and_pending_changes(self):
        rng = random.Random(4)
        for _ in range(3000):
            lo = rng.randrange(100)
            hi = lo + rng.randrange(1, 100)
            # New lists between parts simulate completed/new GPU writes; prefix is never persistent.
            for pending in ([], [(0,lo),(hi,hi+10)], [(lo,hi)],
                            [(a:=rng.randrange(220), a+rng.randrange(1,50)) for _ in range(25)]):
                first = next((i for i,(a,b) in enumerate(pending) if a < hi and b > lo),len(pending))
                for _ in range(4):
                    a=rng.randrange(lo,hi); b=rng.randrange(a+1,hi+1)
                    self.assertEqual(copies(a,b,pending),copies(a,b,pending,first))
    def test_descriptor_consumers(self):
        for split in range(5):
            for total in range(split,6):
                for templates in ((False,False),(True,False),(False,True),(True,True)):
                    for sets in (1,2):
                        old={i:i for i in range(total) if not all(templates[:sets])}
                        new={i:i for i in range(total) if not templates[int(sets==2 and i>=split)]}
                        for i in range(total):
                            s=int(sets==2 and i>=split)
                            if not templates[s]: self.assertEqual(old[i],new[i])
    def test_local_initialization(self):
        for n in (0,1,15,16,17,1024):
            for optional in (False,True):
                for enabled in ((True, optional),):
                    for use in enabled:
                        if not use:
                            continue
                        old=bytearray(16*56) if n<=16 else bytearray(n*56)
                        local=bytearray([0xa5])*(16*56)
                        if n<=16:
                            local[:n*56]=bytes(n*56)
                            new=local[:n*56]
                            self.assertEqual(local[n*56:],bytes([0xa5])*((16-n)*56))
                        else:
                            new=bytearray(n*56)
                        self.assertEqual(old[:n*56],new)
        for name in ('vk_texture.c','vk_astc.c'):
            src=(ROOT/'runtime'/name).read_text()
            self.assertNotIn('_local[16] = {0}',src)
            self.assertIn('memset(',src)

if __name__ == '__main__': unittest.main()
