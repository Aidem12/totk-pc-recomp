"""Dirty-census opt-in checks and a shared write-generation differential model.

Run with python tests/dirty_census_test.py. No game data or Vulkan device needed.
These are source checks and a model, not execution of the C runtime or Linux ioctl.
"""
import itertools
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def function(text, name):
    start = re.search(r"\b" + name + r"\([^;{}]*\)\s*\{", text).end()
    depth = 1
    for end in range(start, len(text)):
        depth += (text[end] == "{") - (text[end] == "}")
        if not depth:
            return text[start:end]
    raise AssertionError(name)


class Generations:
    """Reset-on-query write watch shared by two consumers through stamped pages."""
    def __init__(self):
        self.pending = set()
        self.pages = [0, 0]
        self.generation = 0
        self.seen = [0, 0]

    def query(self):
        self.generation += 1
        for page in self.pending:
            self.pages[page] = self.generation
        self.pending.clear()

    def check(self, consumer):
        self.query()
        changed = tuple(p > self.seen[consumer] for p in self.pages)
        self.seen[consumer] = self.generation
        return changed


class DirtyCensusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.memory = (ROOT / 'runtime/vk_memory.c').read_text(encoding='utf-8')

    def test_submit_gate_precedes_all_diagnostic_work(self):
        body = function(self.memory, 'vk_mem_dirty_submit').lstrip()
        self.assertTrue(body.startswith('if (!dirty_census || !(rt_trace & RT_TRACE_PERF) || vk_mirror_on) return;'))
        # Exhaust the gate's boolean combinations, including the old mirror exclusion.
        expression = body[body.index('(') + 1:body.index(') return;')]
        expression = expression.replace('rt_trace & RT_TRACE_PERF', 'perf')
        expression = expression.replace('||', ' or ').replace('!', 'not ')
        for dirty_census, perf, vk_mirror_on in itertools.product((0, 1), repeat=3):
            skip = eval(expression, {'__builtins__': {}}, locals())
            self.assertEqual(not skip, bool(dirty_census and perf and not vk_mirror_on))

    def test_summary_gate_and_startup_default(self):
        self.assertIn('if (dirty_census && (rt_trace & RT_TRACE_PERF) && !vk_mirror_on) dirty_frame(frame);',
                      function(self.memory, 'vk_mem_census'))
        self.assertIn('dirty_census = rt_knob("DIRTY_CENSUS", "0");', function(self.memory, 'mirror_mode'))
        self.assertEqual(len(re.findall(r'\bdirty_census\s*=', self.memory)), 1)
        self.assertIn('"DIRTY_CENSUS"', (ROOT / 'runtime/cfg.c').read_text(encoding='utf-8'))

    def test_required_consumers_keep_their_queries(self):
        self.assertIn('query_stamp(a, b, g)', function(self.memory, 'shadow_sync_pool'))
        self.assertIn('ww_query(a, b - a)', function(self.memory, 'snap_regions'))
        self.assertIn('ww_query(lo, hi - lo)', function(self.memory, 'vk_mem_changed'))
        for name in ('shadow_sync_pool', 'vk_mem_shadow_sync', 'snap_regions', 'vk_mem_changed_snap',
                     'vk_mem_changed', 'query_stamp', 'vk_gpu_written'):
            self.assertNotIn('dirty_census', function(self.memory, name))
        self.assertIn('vk_mem_shadow_sync();', (ROOT / 'runtime/vk_sync.c').read_text(encoding='utf-8'))

    def test_optional_observer_does_not_lose_writes(self):
        # Writes to two pages; optional diagnostic query; two independently reading consumers.
        # Check events before/after other consumers and the census, including repeated writes.
        for events in itertools.product(range(5), repeat=6):
            old, new = Generations(), Generations()
            for event in events:
                if event < 2:
                    old.pending.add(event)
                    new.pending.add(event)
                elif event == 2:
                    old.query()
                else:
                    self.assertEqual(old.check(event - 3), new.check(event - 3), events)
            for consumer in range(2):
                self.assertEqual(old.check(consumer), new.check(consumer), events)


if __name__ == '__main__':
    unittest.main(verbosity=2)
