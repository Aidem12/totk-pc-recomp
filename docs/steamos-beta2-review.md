# Steam Deck Edition Beta 2: runtime review and first experiment

Baseline: `f0945cc`, clean `develop-steamos`. Work branch:
`perf/steamos-beta2-dirty-census`. Windows checkout only. Target: Steam Deck LCD,
Aerith, Zen 2 4C/8T, RDNA 2, shared memory, SteamOS/RADV, 8 W.

The user's measurements (replay 21-26 ms, GPU 10-19 ms, variable 1% lows) suggest
CPU-side pressure but do not attribute it to a function. No raw captures were
provided for this review. Scene, resolution, cache state and clocks must be held
constant before interpreting changes. No FPS gain is established here.

## Existing work retained

The QUADS, LINE_LOOP and byte-index scratch paths, uniform-copy bounding interval,
last-write-range check, and small local upload/ASTC arrays already exist. They are
unchanged. See `steamos-runtime-optimizations.md`; its old uncommitted-work and
compiler-status notes describe that earlier review, not the current Git state.
The installer localization is committed in the baseline and is untouched.

Existing upstream work also includes descriptor signatures and generation checks,
per-submission texture snapshots, per-region maximum generations, frozen-region
skips, pool-resolution caches, graphics pipeline libraries, disk pipeline caches,
prebuild queues and worker priority promotion. None is a new proposal below.

## Prioritized opportunities

Evidence: **code** means the operation exists, not that it dominates Deck timings.
All performance benefits require Deck measurements. Complexity/risk are relative.

| Priority | Location and evidence | Proposed change and Deck benefit | Complexity / risk | Validation |
| --- | --- | --- | --- | --- |
| 1, implemented | `vk_memory.c`: `vk_mem_dirty_submit` collects buffer-bound pools, takes pool/gen locks, queries write watch and scans page generations every submission with `perf` and no mirror. `dirty_frame` collects and logs the census. | Make only this exhaustive diagnostic opt-in. Remove its scans, queries and locking from normal perf captures. CPU/measurement overhead; no intended improvement with perf already off. | Low / low-to-medium pending native validation | Gate matrix, unchanged active scanner and coherence functions, shared-generation model; compare all three modes on Deck. |
| 2 | `vk_exec_draw.c`: `bind_sig` calls `ubo_ptr` for every `l->p` entry, then calls it again if `OV.n`. `ubo_ptr` can scan `UPD` backwards and resolve a pool. | Reuse a pointer result within this signature evaluation, not across draws. Up to `l->np` repeated reads avoided in the overlay case. CPU benefit independent of memory bandwidth over PCIe. | Small / medium | Prove duplicate resolution has no required side effect, establish guest-write concurrency contract; compare signatures with overlays, partial updates, changed pointers and bind-skip check mode. Do not change signature ordering or overlay lookup. |
| 3, additional | `vk_texture.c`: `vk_texture_write_back` allocates `nowned+1` pointers even if `owned_dirty` returns no hits. `vk_texture_write_back_small` traverses owned textures to count, allocates, then traverses again. | Bounded local candidate storage with overflow fallback, or a proven empty-candidate fast path. Reduce allocator traffic/lock hold time on the four-core CPU. Existing upload metadata changes do not cover these functions. | Small-medium / medium | Record calls, hit counts and candidate capacity; compare candidate ordering, GPU sequence, fence eligibility, CPU rewrites and grass readbacks. Preserve ownership locks and `small_pending`. |
| 4 | `vk_memory.c`: `shadow_sync_pool` queries each touched pool independently; nearby/overlapping pools can cause repeated range queries. It already skips frozen regions and pages behind `reg_max`. | Design merged queries at the actual pre-submit shadow synchronization boundary, retaining per-pool generations and copies. Potential CPU/syscall saving, frequency unknown. | High / high | Trace overlapping ranges and query cost; model writes between queries, untracked fallbacks, overlapping pools, pending GPU writes and intermediate splits. Do not simply share the earlier texture snapshot: it may miss later CPU writes. |
| 5 | `vk_pipeline.c`: `workers_start` chooses two regular workers for eight usable logical CPUs; library prebuild and translation have separate limits. `pipe_worker` already promotes urgent jobs and lowers speculative priority. | First measure aggregate workers and demanded-build latency; then consider a bounded budget for speculative work while replay is busy. Avoid increasing thread counts. Potential p99 improvement; no demonstrated scheduling defect. | Medium-high / medium-high | Warm/cold caches separately, urgent queue delay, context switches, CPU/GPU clocks and p99 at 8 W. Ensure warm-up progress and preserve shader semantics. |
| 6, additional | `vk_exec.c`: `view_of` linearly scans `ex->views` for unregistered targets; registered textures already use their own view path. Views are retained until the command-buffer fence. | If misses/hits and list sizes justify it, add a last-view check or submission-local lookup. Reduce repeated comparisons without global lifetime changes. | Small-medium / medium | Count fallback calls/list lengths first. Compare every key field, reuse, destruction and fence retirement; validate image views. No cross-frame cache without an ownership design. |
| 7 | `vk_rec.c`: publication batching already exists; `rec_drain` spins then yields. AMD recorder defaults off with a documented driver-stability rationale. `vk_sync.c` splits work and submits prologue before replay commands. | Investigate only after captures establish an active recorder or excessive split cost. Do not implement a hybrid wait, change splits, queue order or defaults in this patch. | Medium-high / high | Producer/recorder/queue-lock timing, submit count and GPU idle gaps, synchronization validation. Work on an inactive recorder cannot improve the target run. |
| Separate installer task | `installer/steps.py`: `Prepare.run` generates the base overlay, all 15 non-16:9 `ASPECT_SIZES`, and control variants. `Prepare.key` fingerprints extraction, runtime and preparation tools. | A Deck-specific profile generates the base 16:9 overlay, `1280x800`, and existing required control variants, omitting 14 aspect passes. Preserve the general installer. Installation time/disk benefit only. | Medium / medium | Include selected profile/aspects in the preparation key; check profile switching, missing manifests, update/resume and control variants. Do not silently omit outputs while retaining the same cache key. |

New measurement finding: `vk_sync_submit` calls `vk_mem_dirty_submit` under
`sub_lock` **before** `perf_begin`, which starts the replay measurement. Removing
that work need not reduce reported replay ms by the same amount. Measure total
render/CPU time and frame percentiles as well. This finding is not an additional
code modification.

## Evaluation of the six original suggestions

1. Heavy telemetry: selected, but deliberately limited to dirty-page enumeration.
   `vk_mem_census` also performs periodic pool/traffic/budget summaries and updates
   `mirror_frame`; disabling the whole function would be broader than necessary.
2. Descriptors: cache already present; only the duplicate within-call pointer
   resolution remains a specific candidate. No discarded bind-signature patch is restored.
3. Memory synchronization: relevant to Linux shadow storage, but read/reset write
   tracking is shared. Requires a design rather than a blanket cache or skipped query.
4. Pipelines: substantial prebuild/GPL/cache machinery already exists. Identify
   demanded compilation versus speculative contention before changing policy.
5. Replay/recorder: defaults and queue protocol unchanged. Prioritize the actual
   recorder-off path used on AMD rather than assume the worker is running.
6. Installer: worthwhile reduction in setup work, isolated from runtime and FPS.

## Implemented change and equivalence argument

`dirty_census` is a restart-only setting, read in `mirror_mode`, called during
`vk_mem_class_log` at device initialization. The environment override is
`TOTK_DIRTY_CENSUS`. Default is `0`; `1` plus `trace=perf` enables the original
scanner and its `mem: dirty` summaries when mirroring is off. No configuration
lookup or new lock is added to each submit. The opt-in scanner body is unchanged.

The scanner is an extra observer of CPU writes, not the synchronization that
updates shadows. Its queries reset write-watch bits only after recording them in
the shared generation table. With it off, writes remain for the required consumers:
`shadow_sync_pool` still calls `query_stamp`; texture checks still use
`snap_regions`/`vk_mem_changed`; GPU write-back still stamps/copies through its
existing path. The runtime already operates without the census when perf is off.
Generation numbers and query timing may differ; exact numeric equality is not the
correct invariant. Required consumers must still detect applicable writes.

Unchanged: shadow copies and pending-write exclusions, texture snapshots/freezing,
barriers, command buffers, queue order, mirror behavior, ordinary traffic/budget
summaries, FPS/frame/replay/GPU statistics, recorder/replay/split defaults and all
seven previous runtime optimizations. `vk_mem_bound_note` stays unchanged to keep
the patch narrow. This does not make all perf tracing free.

## Validation actually performed

- `tests/dirty_census_test.py`: 4 passing tests; source gates/default/required
  queries and a differential shared-generation model over 15,625 event sequences.
  The model covers two pages/two consumers and an optional reset-on-query observer.
  It does not model kernel concurrency, frozen pages, every snapshot-epoch case or Vulkan.
- `tests/steamos_runtime_models.py`: all 7 existing tests pass.
- Nine coherence/bookkeeping functions compared exactly against `f0945cc`:
  `shadow_sync_pool`, `vk_mem_shadow_sync`, `snap_regions`, `vk_mem_changed_snap`,
  `vk_mem_changed`, `query_stamp`, `vk_gpu_written`, `vk_mem_bound_note`, `dirty_frame`.
  The scanner body after its early guard is also unchanged.
- `tests/settings_doc_check.py`: one pre-existing failure, `DESWIZZLE` missing its
  documentation. Confirmed against baseline config/docs. `DIRTY_CENSUS` adds no
  new documentation problem. The unrelated omission is not repaired here.
- No C compiler found on PATH or in the checked MSYS2 UCRT64/Program Files LLVM
  locations; no repository toolchain directory. No C compilation, runtime link,
  native Linux test or Steam Deck measurement was executed. WSL was not accessed.
- Python syntax, changed/new-file content checks and `git diff --check` are part
  of the final local validation. No game files are involved.

## Steam Deck validation before accepting the experiment

Build the changed `vk_memory.c` and `cfg.c` through the existing Linux Ninja build,
then relink the experimental executable; keep that build's pinned toolchain,
sysroot and flags. Use at most two jobs initially. Do not build an AppImage for
this test. Native compile/link and Deck correctness remain gates, not assumed passes.

Use independent restarts and the same save/route, fixed 8 W, 1280x720,
`render_scale=game`, `operation_mode=handheld`, FPS cap and recorder/replay settings:

| Mode | Settings | Purpose |
| --- | --- | --- |
| Normal telemetry | `trace=perf`, `dirty_census=0` | New default diagnostic path; `mem: dirty` lines absent, normal perf lines present. |
| Exhaustive telemetry | `trace=perf`, `dirty_census=1` | Restore scanner; inspect its queries/frame and scan_ms/frame. |
| Control | Perf tracing disabled | Estimate remaining tracing overhead; use the same external frame capture. |

Repeat in alternating order with warmed caches; test cold pipelines separately.
Collect frame median/p95/p99/1% low, total render time, replay, GPU time/idle gaps,
CPU/GPU clocks, shadow query/copy counts and power. Some query cost may move from
the removed diagnostic to required consumers, so scanner time is not a guaranteed
net saving. Exercise grass/terrain, streaming, readbacks and long sessions; run
validation separately from timing. Retain startup memory-mode logs to confirm
shadow on/mirror off rather than assuming the installed configuration.

To restore the old diagnostic behavior, set `dirty_census=1` with `trace=perf` and
restart. To discard this source experiment later, first preserve any subsequent
work, then revert only the three modified tracked files and remove only the two
new test/report files. No commit, push, merge or release is part of this task.

## Technical references and limits

- [Linux pagemap documentation](https://www.kernel.org/doc/html/latest/admin-guide/mm/pagemap.html):
  `PAGEMAP_SCAN` can observe and re-protect written pages. This supports treating
  queries as stateful; it does not prove our multithreaded code correct.
- [Linux AMDGPU invalidation](https://docs.kernel.org/6.7/gpu/amdgpu/driver-core.html):
  userptr invalidation can delay submissions. This supports retaining shadow's
  rationale, not attributing a specific measured cost to the Deck kernel.
- [Mesa RADV](https://docs.mesa3d.org/drivers/radv.html): RADV is the Deck Vulkan
  driver. Driver/compiler behavior must be checked against the installed version.
- [Khronos pipeline caches](https://docs.vulkan.org/guide/latest/pipeline_cache.html)
  and [AMD RDNA guide](https://gpuopen.com/learn/rdna-performance-guide/): reuse
  pipeline caches and avoid time-critical compilation. Vendor guidance is not
  proof of optimal worker policy for RADV on a four-core APU.

UMA does not remove CPU/GPU synchronization or make every host-memory access free.
Avoid importing discrete-GPU PCIe assumptions into this fork. Reducing speculative
CPU work may help the shared energy budget, but that remains a measured hypothesis.


## Integral follow-up: implementation state

This section supersedes the earlier single-experiment status above. Work stays on
`perf/steamos-beta2-dirty-census` at `f0945cc`; no commit is created. The five
pre-existing pending files were copied into an additional ZIP backup before edits
(`beta2-before-integral.zip`, outside the repository in the Codex workspace).

### Implemented, independently reviewable changes

| Change | Files/functions | Structural saving | Proof boundary and remaining risk |
| --- | --- | --- | --- |
| Complete dirty-census documentation | `totk.ini.example`; existing `vk_memory.c`, `cfg.c`, `docs/settings.md` | Previously implemented: no exhaustive scans during ordinary perf tracing. | Default 0 and restart initialization unchanged; required memory consumers unchanged. Not a normal-run FPS improvement when perf was already off. |
| Reuse only immutable pointer results within a signature | `vk_exec_draw.c`: `ubo_ptr`, `bind_sig`; ordinary `ssbo_info` call passes NULL | Avoid a second `upd_read` backward scan and 8-byte copy per applicable pointer with overlays. At most four entries (`RS_PTRS`). | `ubo_ptr` reports stable only for invalid/short bindings or a fully covering command-payload update. Raw guest reads, partial updates, failed resolutions and another-fence updates keep the second lookup/read. No cache across draws. Extra small local arrays/branches may outweigh savings when hits are rare; measure. |
| Small write-back candidate lists | `vk_texture.c`: `WbHits`, `wb_hit_add`, `owned_dirty`, `vk_texture_write_back` | No malloc/free for 0..16 candidate pointers. At candidate 17, one heap allocation and a 128-byte pointer copy. | tex_lock protects nowned and lifetime. Heap capacity is nowned, at least the number appended; size_t multiplication avoids signed nowned+1 arithmetic. No lock or GPU operation moves. On x86-64 the local payload is 128 bytes plus bookkeeping. |
| Single-pass fence candidate gathering | `vk_texture.c`: `vk_texture_write_back_small` | Remove the full counting pass and repeat wb_wanted checks; share bounded local storage. Pending future-fence candidates do not cause allocations. | Same eligibility, notes, small_pending release store, storage_rewritten checks and stable gpu_seq ordering. Heap fallback can reserve more pointers than the old exact qualifying count when nowned is large and sparse; this is the tradeoff for one allocation and one traversal. |
| Deck preparation profile, not an FPS optimization | `installer/cli.py`, `installer/steps.py`: profile helpers and `Prepare` | 14 aspect passes omitted: 19 patch-output attempts become 5. Keeps icon variants. | Explicit CLI selection; general default preserved. Profile/aspects enter keys and output checks, selection survives cancellation. Existing unused aspect folders remain. Old installations reprepare once to acquire profile metadata. |

The pointer optimization deliberately does not reuse arbitrary guest-memory reads:
`vk_mem_resolve` can touch shadow pools or mark physical backing used. The new
stable result exits before resolution, so only branches with no such effect are
reused. `UPD` entries are recorded command payloads; neither `ov_at` nor `ov_rest`
changes them between the two locations inside bind_sig. Partly covering latest
updates still fall back to guest memory. Bind-skip check mode and signature order
remain unchanged. Per-stage bounds and the `rs_list` overflow rejection remain.

Candidate pointers are consumed synchronously by the unchanged write_back_n and
its readback path. Only candidate-list storage is local; no GPU staging payload
moves to the stack. The NULL-list query used by vk_texture_cpu_stale still returns
on the first hit without allocating. Sorting, CPU rewrite filtering, counters,
fences and resource retirement remain in the original order. Heap failures still
use the existing fatal error; small cases no longer have that allocation failure.

The seven prior optimizations are not rewritten. In particular, QUADS/LINE_LOOP,
byte-index conversion, uniform flush bounds, note_written, upload_cpu and
vk_astc_record retain their previous implementations.

### Investigated but not implemented

- `view_of` in vk_exec.c: only the unregistered-target fallback scans `ex->views`;
  registered textures already use the hashed view cache in vk_exec_draw.c. The
  temporary list resets for a submission and its views retire at the fence.
  No Deck list-size/hit-count evidence justifies another lookup structure yet.
- Shadow/write watch: `shadow_sync_pool` queries at the pre-submit boundary;
  snapshots taken earlier for texture validation cannot replace it safely.
  Required design: determine actual overlapping ranges, observe writes between
  queries, retain per-pool generations and GPU-pending-byte exclusions, and test
  frozen/untracked cases and intermediate submits. No query merging implemented.
- Pipeline deduplication: `lib_get` installs `lib_new` before building and waits
  on or steals an existing job; `pipe_miss` marks the cached Pipe building under
  P.lock. Existing keys, GPL and worker promotion already solve obvious duplicate
  work. No speculative concurrency policy is changed without scheduler evidence.
- Recorder/submit: cached tail, packet publication batching and command ownership
  already exist. AMD defaults and all barriers/fences remain unchanged.
- Buffer resolution: the existing 512-slot per-thread cache is generation-checked;
  bypassing it or its shadow_touch side effect is not justified.
- Upload metadata: small upload/ASTC arrays already exist from the prior work.
  Larger retained staging or pixel buffers would alter memory pressure/lifetime;
  defer until format/size and allocation-latency profiles establish a benefit.
- General descriptor/view caching already exists. No persistent pointer cache,
  draw reorder, changed shaders, shadow disablement or extra worker is introduced.

### Tests and compilation status

Runtime tests executed with Python 3.14:

- `dirty_census_test.py`: 4 pass (existing source checks and generation model).
- `beta2_runtime_test.py`: 4 pass. Pointer model includes ring eviction, partial
  coverage, another fence, absent/short bindings, changing guest data between the
  reads and changed updates between draws. Candidate tests cover capacities
  0/1/15/16/17/31/32/65/1024 plus 5,000 seeded random combinations of eligibility,
  fences, trace notes, CPU rewrites and equal GPU sequence values. Source checks
  connect these models to the changed branches. These do not execute compiled C.
- `steamos_runtime_models.py`: all 7 existing tests pass.

Installer tests exercise the real Prepare.run and runner planner with mocked
tool executions and synthetic public output files, without reading game data:

- `installer_profile_test.py`: 6 cases cover exact Deck/general outputs, cached
  reruns, both profile-switch directions, missing manifests, cancel/restart,
  persisted profile during release updates, optional icon failure and old records.
- `installer_test.py`: 53 pass.
- `installer_gui_test.py`: 64 pass, 2 skipped (real build/install-test fixture absent).
  Includes the existing graphical selftest across five languages on Windows.
- CLI `--help` exposes both choices. No actual game patch generation was run.

The Windows sandbox rejects the restrictive ACL created by Python mkdtemp with
mode 0700. Test execution used a process-local mkdtemp wrapper creating unique
directories under ignored build/beta2-tests with inherited workspace permissions.
No system permissions, production code, hooks or global test configuration changed.

The native runtime build was actually attempted with:

```text
C:\Python314\python.exe -B tools/build_game.py --make-runtime-lib --out build/beta2-runtime --jobs 2
```

It exited before compilation: `C:\msys64\ucrt64\bin\clang.exe not found`.
No compatible clang/gcc/ninja was found on PATH; the checked MSYS2 and Program Files
LLVM locations and repository toolchain directory were absent. WSL enumeration
failed with `Wsl/E_ACCESSDENIED`; it was not retried and no Linux checkout changed.
Thus no changed C unit, native harness or integrated runtime has been compiled;
there are no compiler warning results to claim. A Vulkan run is also outstanding.

Use the established Linux toolchain/sysroot/dependency paths, synchronize this
patch deliberately to the experimental build checkout, then rerun its existing
Ninja targets with `-j2`. The runtime-only entry point supports:

```sh
python3 tools/build_game.py --make-runtime-lib --out build/beta2-runtime --jobs 2 \
  --toolchain "$LLVM_ROOT" --deps "$DEPS_ROOT" --sysroot "$SNIPER_SYSROOT"
```

These variables must name the already prepared project SDK, not arbitrary host
libraries. The build script also checks generated/<version>/layout.json; reuse the
existing matching local generated layout. Do not publish it or download game data.
First confirm the C units and then the complete archive build; relink the existing
experimental game separately. An archive build is not a complete executable link.

`settings_doc_check.py` still reports only the pre-existing DESWIZZLE documentation
omission. The new option is now in both docs/settings.md and totk.ini.example.
Final validation also includes syntax checks, content guard on every changed/new
file, baseline function comparisons and git diff --check.

### Deck acceptance gates and review boundaries

No optimization here has demonstrated FPS or frametime improvement. Test each
runtime change independently, then combined, at the same 8 W, route/save, warmed
cache, 1280x720, handheld and recorder/replay settings. Collect p95/p99 frame time,
total render/replay and GPU timing. For pointers use bind_skip=check in a separate
correctness run; for write-back exercise grass, overlapping textures, delayed
fences, CPU overwrites and streaming, with Vulkan validation in a separate run.
Check heap fallback frequency and memory before asserting the one-pass list wins.

Review/stage separately later: census option, immutable pointer reuse, candidate
storage plus its one-pass consumer (shared helper), and installer profile. Tests
and documentation belong with their corresponding change. Nothing is committed,
staged, pushed, merged, rebased or released in this task.
