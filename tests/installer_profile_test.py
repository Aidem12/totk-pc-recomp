"""Exercise real Prepare and runner planning with public, synthetic tool outputs only."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from installer import steps, runner, state
from installer.events import Reporter


class ExtractInput(runner.Step):
    id = 'extract'

    def key(self, ctx):
        return 'extract-key'


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        source = root / 'source'
        source.mkdir()
        self.dest = root / 'dest'
        self.dest.mkdir()
        self.temp = root / 'temp'
        self.temp.mkdir()
        xlate = source / 'totk_xlate'
        xlate.write_bytes(b'public test fixture')
        self.layout = SimpleNamespace(xlate=xlate, tools=source, shader_list=source / 'shaders.tsv', release='test')
        self.layout.shader_list.write_text('public fixture')
        self.output = io.StringIO()
        self.ctx = runner.Context(self.layout, self.dest, self.temp, Reporter(out=self.output),
                                  SimpleNamespace(prepare_profile=None))
        self.ctx.keys['extract'] = 'extract-key'
        self.calls = []
        self.cancel = False
        self.fail_prompts = False
        self.addCleanup(patch.stopall)
        patch.object(steps, 'runtime_key', return_value='runtime-key').start()
        patch.object(steps, 'shader_list_text', return_value='shader-list').start()
        patch.object(steps, 'run_tool', side_effect=self.tool).start()

    def tool(self, ctx, step, script, args, parse=None, **kwargs):
        self.calls.append((Path(script).name, list(args)))
        if self.cancel:
            raise KeyboardInterrupt
        if Path(script).name == 'make_romfs_patches.py':
            out = Path(args[args.index('--out') + 1])
            if self.fail_prompts and '--prompts' in args:
                return 1, 'test.log'
            out.mkdir(exist_ok=True)
            (out / 'manifest.tsv').write_text('public fixture')
        elif Path(script).name == 'prepare_shaders.py':
            (self.dest / 'shader-prepared.bin').write_bytes(b'public fixture')
        return 0, 'test.log'

    def run_prepare(self):
        self.ctx.keys = {}  # a new installer invocation computes a fresh plan
        self.ctx.steps = [ExtractInput(), steps.Prepare()]
        result = runner.run_steps(self.ctx.steps, self.ctx)
        self.assertEqual(result, 'ok', self.output.getvalue())
        return self.ctx.record('prepare')

    def patch_outputs(self):
        return [Path(args[args.index('--out') + 1]).name for name, args in self.calls
                if name == 'make_romfs_patches.py']

    def test_deck_outputs_and_incremental_skip(self):
        self.ctx.options.prepare_profile = 'steam-deck'
        rec = self.run_prepare()
        self.assertEqual(self.patch_outputs(), ['romfs-patches', 'romfs-patches-1280x800',
                                               'romfs-patches-xbox', 'romfs-patches-xbox-label', 'romfs-patches-pc'])
        self.assertTrue(steps.Prepare().current(self.ctx, rec))
        self.calls.clear()
        self.ctx.options.prepare_profile = None
        self.run_prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(state.load(self.dest)['prepare_profile'], 'steam-deck')

    def test_general_switch_and_missing_output(self):
        self.ctx.options.prepare_profile = 'steam-deck'
        deck = self.run_prepare()['key']
        self.ctx.options.prepare_profile = 'general'
        self.calls.clear()
        rec = self.run_prepare()
        self.assertNotEqual(deck, rec['key'])
        self.assertEqual(len(self.patch_outputs()), 19)
        self.assertTrue(steps.Prepare().current(self.ctx, rec))
        (self.dest / 'romfs-patches-2560x1080' / 'manifest.tsv').unlink()
        self.assertFalse(steps.Prepare().current(self.ctx, rec))
        self.calls.clear()
        self.run_prepare()
        self.assertEqual(len(self.patch_outputs()), 19)
        # Changing back also changes the key, while old extra folders are not deleted.
        self.ctx.options.prepare_profile = 'steam-deck'
        self.calls.clear()
        self.assertEqual(self.run_prepare()['key'], deck)
        self.assertEqual(len(self.patch_outputs()), 5)
        self.assertTrue((self.dest / 'romfs-patches-2560x1080').is_dir())

    def test_cancelled_switch_persists_selection_not_completion(self):
        original = self.run_prepare().copy()
        self.ctx.options.prepare_profile = 'steam-deck'
        self.cancel = True
        self.ctx.keys = {}
        self.assertEqual(runner.run_steps([ExtractInput(), steps.Prepare()], self.ctx), 'cancelled')
        saved = state.load(self.dest)
        self.assertEqual(saved['prepare_profile'], 'steam-deck')
        self.assertEqual(saved['steps']['prepare'], original)
        self.ctx.state = saved
        self.ctx.options.prepare_profile = None
        self.cancel = False
        self.calls.clear()
        self.run_prepare()
        self.assertEqual(len(self.patch_outputs()), 5)

    def test_defaults_legacy_and_optional_prompts(self):
        self.assertEqual(steps.prepare_aspects(self.ctx), steps.ASPECT_SIZES)
        self.fail_prompts = True
        rec = self.run_prepare()
        self.assertTrue(steps.Prepare().current(self.ctx, rec))
        legacy = {'xlate_sha256': rec['xlate_sha256']}
        self.assertFalse(steps.Prepare().current(self.ctx, legacy))
        self.ctx.options.prepare_profile = 'invalid'
        with self.assertRaises(runner.InstallError):
            steps.Prepare().key(self.ctx)

    def test_deck_missing_manifest_and_release_update(self):
        self.ctx.options.prepare_profile = 'steam-deck'
        rec = self.run_prepare()
        (self.dest / 'romfs-patches-1280x800' / 'manifest.tsv').unlink()
        self.assertFalse(steps.Prepare().current(self.ctx, rec))
        self.calls.clear()
        self.run_prepare()
        self.assertEqual(len(self.patch_outputs()), 5)
        saved = state.load(self.dest)
        self.layout.release = 'updated-release'
        self.ctx = runner.Context(self.layout, self.dest, self.temp, Reporter(out=self.output),
                                  SimpleNamespace(prepare_profile=None), state=saved)
        self.assertEqual(self.ctx.mode, 'update')
        self.calls.clear()
        with patch.object(steps, 'runtime_key', return_value='updated-runtime'):
            rec = self.run_prepare()
        self.assertEqual(rec['profile'], 'steam-deck')
        self.assertEqual(rec['release'], 'updated-release')
        self.assertEqual(len(self.patch_outputs()), 5)

    def test_profile_does_not_change_upstream_keys(self):
        # Only Prepare.key reads the profile; confirm the extract/compile inputs remain identical.
        self.assertEqual(self.ctx.keys['extract'], 'extract-key')
        first = steps.Prepare().key(self.ctx)
        self.ctx.options.prepare_profile = 'steam-deck'
        self.assertNotEqual(first, steps.Prepare().key(self.ctx))
        self.assertEqual(self.ctx.keys, {'extract': 'extract-key'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
