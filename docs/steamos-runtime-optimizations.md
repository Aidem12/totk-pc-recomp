# Steam Deck LCD runtime optimization review

Target: AMD Aerith, Zen 2 (4 cores / 8 threads), RDNA 2, shared LPDDR5,
SteamOS and RADV. These changes do not establish an FPS improvement.

Starting commit: `b8b948e3e21d8c852204310e7dafc52e873beadc`, on top of official
v0.3.13. Local backup: `backup/steamos-before-runtime-optimizations`.
The existing QUADS change remains byte-for-byte unchanged.

## Implemented changes

| Change | Location | Structural saving | Limits |
| --- | --- | --- | --- |
| Direct LINE_LOOP generation | `vk_exec_draw.c`, `op_draw` | One malloc/free pair and a `4 * (count + 1)` byte copy per applicable draw | Mapped-memory performance still needs measurement |
| Direct byte-index widening | `vk_exec_draw.c`, `op_draw_idx` | One malloc/free pair and a `2 * count` byte copy | Keeps the host-readable backing check and 0xff to 0xffff restart conversion |
| Uniform-copy bounding interval | `vk_exec_draw.c`, `vk_exec_ub_flush` | Skips exact comparisons when a range cannot overlap any copy since the last barrier | Worst case remains quadratic; overlapping cases pay a small extra test |
| Last-added write range first | `vk_exec_draw.c`, `note_written` | Consecutive duplicates of the last appended range need one comparison | Earlier duplicates can need one extra comparison; no claim about their frequency |
| Local small upload metadata | `vk_texture.c`, `upload_cpu` | One calloc/free pair, or two with GPU ASTC, for at most 16 regions | Adds bounded stack storage; larger arrays keep the heap fallback |
| Local small ASTC metadata | `vk_astc.c`, `vk_astc_record` | Two calloc/free pairs for at most 16 regions | Same bounded-stack tradeoff; no shader changes |

`bind_index` had no remaining callers after the two index changes and was removed.
No memory pools, image layouts, descriptors, command ordering, shader code,
graphics settings, recorder settings, or synchronization primitives were changed.

### Correctness arguments

Indices retain the original generation loops, types and draw arguments. Only the
destination changes. Scratch is host-visible and host-coherent, and offsets are
256-byte aligned. Its allocations belong to the submission's `CbRes` and are
recycled by `cb_done` after the completion fence. Allocation failures still reach
the existing fatal paths. The removed temporary allocation can no longer fail.
Byte indices still read guest backing as before; this does not add a snapshot or
change the guest memory synchronization contract.

The largest positive signed 32-bit count requires 8 GiB for LINE_LOOP and
4,294,967,294 bytes for byte-index widening. Size expressions use 64-bit arithmetic;
the generated counts fit uint32_t. Tests check arithmetic at these extremes,
not successful allocation of these sizes.

The uniform bound is the minimum start and maximum uint64_t end of copies since
the last barrier. An overlap with any individual interval implies an overlap with
these bounds. The exact original predicate still decides whether a barrier is
recorded. Bounds reset with `since` after that barrier, then include the current
copy. Tests also preserve the old predicate's behavior for zero lengths and
wrapped additions; this is not a new validation policy for invalid ranges.

Write-range deduplication preserves exact address/size equality, insertion order
and the pending-write notification count. It does not merge adjacent or overlapping
ranges and introduces no shared cache or locks.

Local upload arrays initialize exactly the used portion, as calloc did. Only
heap-backed arrays are freed. Their pointers remain valid through ASTC check mode
and all upload chunks. `vk_astc_record` consumes region descriptions synchronously.
The recorder's `XFER_CMD` wrappers copy region arrays into packets; they do not
retain pointers into the caller's stack. Vulkan buffers and GPU payloads remain
unchanged. Failed heap metadata allocation now takes an explicit fatal path in
`upload_cpu` instead of proceeding with a null pointer.

## Validation

Run without game data:

```text
python tests/steamos_runtime_models.py
git diff --check
```

The Python suite covers the independent index routes, boundary sizes around a
4 MiB chunk, unsigned index wraparound, primitive restart on/off, output guards,
exact deduplication and metadata capacity boundaries (0, 1, 15, 16, 17 and larger).
The barrier model compares exact barrier positions for 2,401 exhaustive sequences
and 10,000 seeded random sequences, including adjacent, contained and wrapping
ranges. Deduplication covers 15,625 exhaustive sequences. ASTC metadata checks
cover reuse across chunks. Source checks ensure QUADS remains unchanged.

Synthetic structural examples, not game measurements:

- 1,000 adjacent uniform ranges: 499,500 exact overlap comparisons become zero,
  with identical barrier positions.
- 1,000 distinct write ranges followed by 1,000 repeats of the last: 999,000 fewer
  equality comparisons, with the identical ordered notification list.

These are differential models and source invariants, not execution of the C
runtime. No C compiler was found on PATH or in the previously checked MSYS2/LLVM
locations. No partial build, full build, Vulkan run or Steam Deck measurement has
been performed. Overall integration confidence is **medium**; algorithmic
equivalence has stronger evidence than integration or performance.

Executed locally: all seven model/source tests passed, `git diff --check` passed,
and the content guard's `problems_for` check passed on all five modified/new files
explicitly (including the untracked test and this document). The index was not
changed; an empty `--staged` check is not counted as validation of these changes.

## Git hook blocker

The configured hook directory is `.githooks`; `pre-commit` uses `#!/bin/sh` and
executes `python tools/check_content.py --staged`. The file is readable and its ACL
includes read/execute access. The interpreter exists at
`C:\Program Files\Git\usr\bin\sh.exe`.

Even invoking that interpreter with `-c 'exit 0'` fails before executing a script:

```text
NtCreateDirectoryObject(\BaseNamedObjects\msys-2.0S5-1888ae32e00d56aa): 0xC0000022
```

This reproduces an MSYS initialization access failure independently of the hook's
contents. Changing its shebang, Python command or repository ACL would not address
the observed failure. No global Git configuration, hook disabling, permissions
workaround or hook bypass was applied. Changes are intentionally left uncommitted.

After compiling and reviewing, a normal user PowerShell session can verify that
`sh -c 'exit 0'` and the actual hook work. Stage independent hunks and their tests
separately; do not combine unrelated optimizations into one commit. Suggested
messages: `perf: optimize line loop indices`, `perf: widen byte indices in scratch`,
`perf: bound uniform copy overlap scans`, `perf: check recent write range first`,
`perf: reduce upload metadata allocations`, `perf: reduce ASTC metadata allocations`.

## Deferred opportunities

| Area | Reason not implemented | Evidence needed |
| --- | --- | --- |
| Shared shadow queries | Queries reset write tracking; per-pool generations and pending GPU writes must remain consistent under concurrency | Kernel-query counts, duplicated spans and concurrent-write equivalence tests |
| Shadow-backed GPU deswizzle | Upload batches can submit before `prologue` refreshes shadow, including intermediate flushes | Immutable snapshot design covering pending GPU writes, CPU reference comparisons and synchronization validation |
| Staging object reuse | Adds retained memory and ownership beyond existing batch retirement; readback staging has a separate caller-owned path | Allocation profile, pressure-aware bounded cache and fence/reclamation tests on RADV |
| Deswizzle geometry cache | Existing layout path handles format, levels, arrays and 3D; cross-call caches introduce invalidation, while intra-upload reuse needs a tested geometry interface | Compiled differential tests against real layout helpers across supported formats |
| Large pixel temporaries | ASTC CPU/RGB paths need different storage; retained large buffers can increase peak memory across batches | Actual format/frequency profile and peak allocation measurements |
| Headroom/UMA budgets | Per-type reservations can share a heap, but arbitrary cuts may reintroduce allocation stalls | Heap properties, budgets, RSS and render-thread allocation latency |
| Pipeline worker policy | Eight logical processors already select two regular workers; speculative workers depend on pending work | Scheduler, urgent compilation latency, warm/cold cache and 8 W measurements |
| Recorder spin / submits | Existing batching and queue ownership are deliberate; fewer spins may increase wakeup latency | Recorder-on measurements and synchronization tests; no policy change here |
| Narrower Vulkan barriers | Producer/consumer and aliasing dependencies extend beyond individual calls | GPU captures and synchronization validation before changing scopes |
| NGX exclusion | FXAA handling and motion-related APIs cross module boundaries; removing a file is not safe | Isolated backend/stubs, complete link test and FXAA regression test |
| Intel/proprietary driver branches | Mostly startup decisions; some shared fixes protect coherence and cache synchronization | No demonstrated hot-path gain justifying removal |
| Win32 removal / v3-only package | Win32 already excluded from Linux; package specialization is not a replay optimization | Installer/update tests; packaging remains unchanged |

## Experimental build gate

These changes are candidates for a Linux experimental build after partial runtime
compilation succeeds. None enables a deferred GPU or synchronization design.
Before packaging, run the suite above, compile the changed translation units with
the project's pinned LLVM/sysroot, then test Vulkan validation separately from
performance measurement. Verify FXAA, UI, terrain, index restart, texture streaming,
ASTC check mode and long sessions without new device faults.

At 8 W, compare identical saves/routes and warm shaders, with unchanged resolution,
FPS cap and recorder setting. Record replay CPU time, frame median/p95/p99, GPU
time, allocation counts, QUADS/LINE_LOOP/byte-index frequency, RSS and memory
budgets. Inspect actual scratch memory flags and generated code: direct mapped
writes are not guaranteed faster than malloc plus an optimized memcpy.

Do not generate an AppImage or publish these changes until those results are reviewed.
