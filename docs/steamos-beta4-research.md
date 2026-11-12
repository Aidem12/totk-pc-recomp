# TearsOS Beta 4: v0.4.0 performance research

## Scope and verified starting state

Research date: 2026-11-11. Target: Steam Deck LCD, Aerith, SteamOS/RADV, 8 W, GPU reference 1000 MHz. No runtime modifications, integrations, builds or benchmarks were performed.

The Windows checkout is on `main`, `7a37fc3` (v0.4.0), initially clean. Local references include `perf/steamos-beta3` and `perf/steamos-beta4`, both `d63fe61`; `develop-steamos` is `f0945cc`, `steamos` is `30581ed`. `origin/main`, `upstream/main` and `upstream/dev` locally point to `7a37fc3`. These are local reference observations, not a fresh remote synchronization check.

Remotes: origin = Aidem12/totk-pc-recomp; upstream = valentderah/totk-pc-recomp. The private Aidem12/TearsOS remote is not configured here and was not verified. `stash@{0}` preserves the interrupted Beta 4 work (vertex metadata and shadow prefix/telemetry changes); it was neither applied nor changed. Sandbox Git work-tree checks initially failed; a read-only elevated check confirmed the clean state and stash.

`AGENTS.md` was read. `docs/agent-context.md` was initially absent. Only these two research documents are being created. Existing experimental branches and source files remain intact.

Evidence labels below: **verified** means source behavior, not a measured bottleneck. **Hypothesis** means its performance relevance still needs measurements. Historical timing comments in upstream are not measurements from this investigation or transferable FPS gains.

## Pass 1: system map

| Area | Execution path and existing mechanisms | Potential cost / boundary |
|---|---|---|
| Guest CPU | `recomp/lifter.py`, `recomp/regs.py`, `recomp/flags.py` generate C; `runtime/dispatch.c:rt_dispatch` handles indirect targets with preemption checkpoints. Documentation describes local guest registers and selective reloads. | Generated instruction volume, indirect calls, instruction-cache pressure. No guest code or native profiles were available to identify the dominant functions. Do not add another generic dispatch cache without evidence. |
| Floating point | `runtime/simd_fp.h` has hardware-FMA selection, directed-rounding paths and exceptional-lane repairs. | Exact ARM behavior constrains simplification. Aerith-target build flags and actual slow-path frequency must be checked before proposing changes. Fast-math or dropping NaN/rounding repairs is not justified. |
| Guest scheduling | `runtime/kernel.c:kspin_ns`, `kspin`, `block_inner`; virtual-core ownership, priority waits and asynchronous preemption. | Eight logical CPUs do not provide eight independent physical cores or unlimited power. Polling can compete with guest/render work. Preserve Horizon scheduling semantics. |
| Resource loading | `runtime/romfs.c:read_at` already uses positional reads; `runtime/ov_zstd.c:dict_for` caches native dictionaries and uses a thread-local decompression context. Native patches are documented for guest 1.4.3. | Streaming latency and dictionary lookup, distinct from sustained replay. No basis for a broad ROM cache or assuming these patches affect 1.2.1. |
| Rendering | `vk_exec.c` supports chunked execution; `vk_exec_draw.c` stores draw state in `VkExecDraw` through `ex->draw`. Binding skips, descriptor reuse, snapshots and memory generations already exist. | Prior globals cannot be copied from Beta 3 into concurrent execution contexts. |
| Shadow / UMA | `vk_memory.c:shadow_sync_pool`, `snap_regions`, `vk_mem_shadow_sync`, `vk_mem_shadow_prologue`; region snapshots plus deferred GPU copies and fence guards. | OS queries, page scans, CPU staging copies, GPU transfers and waits are different costs. Snapshot queries are destructive observations, not a pure predicate. |
| Textures / memory | `vk_texture.c:upload_cpu`, write-back paths; `vk_astc.c` supports ASTC decoding/BC3 storage; `vk_memory.c:host_cap`, `vk_mem_vram` account for host availability. | CPU/GPU bandwidth, allocation bursts, quality-sensitive compression and memory pressure. Do not treat reported device-local budget as independent physical RAM. |
| Compilation | `gfx_shader.c` translation/prewarm; `vk_pipeline.c:workers_start`, `prebuild_start`, `lb_start` and warm builders. | Cold-cache stalls and competing workers; warm-cache steady-state may not benefit from compiler changes. |
| Submission / presentation | `vk_rec.c:wait_space`, `rec_drain`; `vk_sync.c` submission/prologue; `vk_backend.c:wait_until`, `present_main`; `frame_rate.c` limit and step control. | Queue dependencies, polling, deadlines and frame pacing. Split work can be inside replay; final prologue work can be outside it. Do not add their durations blindly. |

## Pass 2: Beta 3 compatibility matrix

Classification is a porting recommendation, not authorization to integrate.

| Previous change | v0.4.0 evidence | Classification and conditions |
|---|---|---|
| QUADS / LINE_LOOP direct scratch | `vk_exec_draw.c:op_draw` still allocates indices, calls `bind_index` (scratch copy), then frees. | **Recoverable with adaptation.** Use the current execution context and scratch lifetime; preserve counts, overflow/failure handling and command sequence. Good first isolated port. |
| 8-to-16-bit indices | `op_draw_idx` still allocates/converts/copies/frees. | **Recoverable with adaptation.** Preserve restart mapping, bounds and aligned scratch; test all input bytes and boundary counts. |
| UB overlap interval prefilter | `vk_exec_ub_flush` still scans earlier copies since the most recent barrier. Existing `OV.lo/hi` in `ub_bind` is a different filter. | **Recoverable with adaptation.** Batch bounds must reset at every existing barrier and use current context state; retain exact fallback overlap tests. |
| Last-range `note_written` check | Current function scans `CbRes.written` from the beginning before calling `vk_mem_gpu_pending`. | **Recoverable almost directly.** Guard the empty list; retain full search and pending-write notification on new ranges. Benefit depends on adjacent repeats. |
| Small metadata arrays for uploads / ASTC | `upload_cpu` allocates `rg` and optional `ar`; `vk_astc_record` allocates `copies` and `bcp`. | **Recoverable with adaptation.** Current BC3/decode paths differ: audit initialization, element counts and whether recording copies arguments before local arrays expire. Do not replace pixel buffers with stack storage. |
| Optional dirty census | `vk_mem_dirty_submit` and census in `vk_mem_census` are gated by perf/mirror state; no DIRTY_CENSUS key found. | **Recoverable with adaptation.** Separate exhaustive diagnosis from ordinary perf summaries, preserve required coherence/ownership operations. Improves diagnostic overhead, not an untraced build automatically. |
| Stable `ubo_ptr` result reuse | Current `ubo_ptr` has no stability output; `bind_sig` calls it for signature and overlay processing. | **Recoverable with adaptation.** Reassess snapshots and `drop_n`; preserve second observations for mutable guest data and all resolve side effects. |
| Precomputed overlay binding offsets | `rs_list` stores ranges; `bind_sig` still filters range offsets, bounds and alignment. | **Recoverable with adaptation.** Only immutable offsets may be retained; preserve order/duplicates and current per-context storage, not old global RS state. |
| Last temporary image view | `vk_exec.c:view_of` linearly searches temporary views. Current cleanup includes chunk paths as well as sequential recording. | **Recoverable with adaptation.** Context-local index, full fast-path key, invalidation at every view-list reset; no cross-chunk ownership. |
| WbHits / one-pass collection | `vk_texture_write_back` allocates a candidate array; `vk_texture_write_back_small` counts then collects. Current code also uses `owned_lock`. | **Recoverable with adaptation.** Preserve both locks, fence eligibility, sort/write-back order, notes and small_pending. Do not transplant old lock assumptions. |
| Installer branding / Spanish / Deck profile | `installer/cli.py` and `steps.py` differ from Beta 3; the previous prepare-profile/prepare_profile/steam-deck identifiers are absent there. | **Recoverable with adaptation**, independent of FPS. Reconcile install-state keys, cancellation/resume and current version support. A complete localization inventory was not performed; do not overwrite upstream translations. |
| Earlier differential tests/docs | Tests describe old structures and semantics. | **Recoverable with adaptation**, not proof for current C or a substitute for native tests. |
| Interrupted Beta 4 vertex metadata | Current `bind_vertex_buffers` directly updates `D->B`, retains unused bound streams and binds only the changed interval. | **Already resolved by upstream**, with a broader implementation. Do not apply the stashed patch. |
| Proposed shadow query grouping | Current native-host default SHADOW_SNAP shares region observations across pools/submission parts and retains a fallback. | **Already addressed by upstream.** Do not reintroduce the older observation protocol or duplicate its cache. |
| Stashed shadow prefix / detail counters | Current `shadow_copy` can queue deferred `DRange` entries instead of memcpy; timing/ownership differ. | **Needs measurement and adaptation.** Prefix scan is still plausible but only when multiple copy runs amortize it; old telemetry is incomplete and unvalidated. |

## Ranked opportunities

Ranks combine expected relevance and safe experimental cost, not claimed speedups. N1-N5 are new investigation directions; R1-R3 are deliberate recoveries, not new discoveries.

### N1 — Guest wait polling on a four-core APU (priority high)
- **Location:** `runtime/kernel.c:kspin_ns`, `kspin`, `block_inner`.
- **Verified:** default polling is 20 microseconds unless fewer than six usable logical CPUs; the loop pauses and periodically reads time. The threshold can enable polling on an eight-thread Deck.
- **Hypothesis / benefit:** unnecessary polling could consume CPU time and power needed by the three guest virtual cores and rendering, affecting sustained FPS/energy.
- **Proposal:** first measure existing `TOTK_KSPIN_US=0` versus default, then intermediate values; only consider a Deck-specific policy if repeatable. No new synchronization algorithm initially.
- **Complexity/risk:** low experiment cost, medium performance risk; reducing polls can increase OS wake latency.
- **Validation:** wait duration/handoff distributions, wake latency, CPU frequency/time, p99 and gameplay correctness under priority/contention stress. Preserve all locks, predicates and preemption checks.

### N2 — Presentation deadline tail (priority medium-high)
- **Location:** `runtime/vk_backend.c:wait_until`, called by `present_main`.
- **Verified:** sleeps until approximately 1 ms before a sufficiently distant deadline, then repeatedly reads the clock and yields until that deadline.
- **Hypothesis / benefit:** a fixed tail can consume CPU scheduling/power budget on Linux. It may instead be necessary to avoid late presentation.
- **Proposal:** evaluate a Linux/Deck-specific shorter tail or measured wake-margin policy without changing the target deadline, frame step or FPS limit.
- **Complexity/risk:** medium; late wakes can worsen pacing even when average CPU use falls.
- **Validation:** requested-versus-actual wake/present timestamps, missed deadlines, median/p95/p99 intervals, capped and uncapped control runs. Do not count time blocked/yielding as fully busy CPU.

### N3 — Concurrent compilation budget (priority high for cold caches)
- **Location:** `runtime/vk_pipeline.c:workers_start` selects 2..4 workers from usable logical CPU count; `prebuild_start`, `lb_start` and prewarm paths also schedule work. `vk_exec.c` has optional replay workers (default configuration 0).
- **Verified:** several independent worker mechanisms exist; a visible eight-thread CPU yields two pipeline workers in workers_start. Startup prewarm and gameplay compilation are not assumed concurrent without a trace.
- **Hypothesis / benefit:** concurrent work can compete with guest execution at 8 W; priority/concurrency budgeting could reduce cold-cache frametime spikes.
- **Proposal:** profile active jobs and queue latency first; then test fewer background builders or a coordinated background budget. Preserve priority escalation for required pipelines and existing in-flight deduplication.
- **Complexity/risk:** medium; lower concurrency may prolong missing-pipeline waits. Avoid global scheduler redesign.
- **Validation:** separate cold/warm runs, translation/compile/link time, skipped draws and one-shot completion, job CPU time and p99. Reject a change that only shifts stalls later.

### N4 — Native zstd dictionary lookup (priority low until profiled)
- **Location:** `runtime/ov_zstd.c:dict_for`, `fingerprint`; NDICT=32.
- **Verified:** every lookup fingerprints selected bytes, takes a shared lock and linearly checks dictionary entries. Dictionaries are immutable host copies and never freed; a full table falls back to guest decoding. Native patch sites are for 1.4.3.
- **Proposal:** if a few dictionaries dominate and lookup is material, add a thread-local last host-dictionary entry, still recomputing and comparing the existing guest/content/size/fingerprint key each time; retain the locked miss path and immutable entry lifetime. Do not cache mutable guest bytes or weaken validation.
- **Potential benefit:** CPU/streaming latency; unlikely to change steady-state rendering without frequent dictionary calls.
- **Complexity/risk:** low-medium; key completeness and initialization/lifetime need proof. Benefit may be too small to justify a cache.
- **Validation:** 1.4.3 dictionary hit distribution and lookup share of decode time; concurrent reuse/mutation cases and byte-identical decode results. Not a recommendation to port 1.4.3 patch addresses into 1.2.1.

### N5 — Deferred shadow copy command aggregation (priority medium, measure first)
- **Location:** `runtime/vk_memory.c:shadow_copy`, `vk_mem_shadow_prologue`.
- **Verified:** deferred ranges are packed into staging chunks; the inner loop issues a `vkCmdCopyBuffer` for each portion. Staging packing already exists and must not be reinvented.
- **Proposal:** consider grouping only consecutive regions targeting the same destination buffer and staging buffer into one multi-region copy call, subject to Vulkan validity and exact dependency equivalence. Preserve read timing, range ordering, chunk bounds, barriers and fence retirement; never merge overlapping/hazardous ranges blindly.
- **Potential benefit:** CPU recording/recorder packet overhead; no reduction in payload bytes by itself.
- **Complexity/risk:** medium/high; only proceed with proof that the candidate regions can legally coexist in a copy command. The shared shadow snapshot mechanism is not changed.
- **Validation:** range-count histogram and same-buffer run lengths; differential copy results with overlaps, pending GPU writes, staging boundaries and successive submissions; Vulkan validation and visual tests. May be rejected if valid runs are short.

### R1 — Direct scratch indices (priority high for a first code experiment)
- **Location:** `vk_exec_draw.c:op_draw`, `op_draw_idx`, `bind_index`.
- **Verified:** heap temporary plus scratch memcpy remains in three paths.
- **Proposal:** recover QUADS alone first, then independent LINE_LOOP/byte-index changes if useful.
- **Benefit:** one heap allocation/free and one copy avoided per applicable operation. Call frequency and mapped-memory store cost on RADV are unknown.
- **Complexity/risk:** low-medium. Test extreme counts, restart, alignment, scratch turnover and allocation failure; native code and Deck A/B are required. No aggregate FPS promise.

### R2 — Write-back candidate collection (priority medium-high)
- **Location:** `vk_texture.c:vk_texture_write_back`, `vk_texture_write_back_small`, `owned_dirty`.
- **Verified:** heap candidates and, for small write-backs, two full owned-list passes remain.
- **Proposal:** port WbHits plus single-pass selection under current locks with bounded local capacity and heap fallback.
- **Benefit:** fewer allocations/list traversals; no pixel-copy reduction.
- **Complexity/risk:** medium. Validate empty/large lists, dirty/fence filtering, later flag, notes, order, concurrent ownership changes and fallback allocation failure. Measure owned count, candidate count and call frequency.

### R3 — Separate diagnostic census from ordinary perf (priority high for measurement quality)
- **Location:** `vk_memory.c:vk_mem_dirty_submit`, census path; `cfg.c`.
- **Verified:** perf enables exhaustive census when mirror mode is off; the Beta 2 optional key is absent.
- **Proposal:** recover the opt-in census gate without skipping any required shadow/write-watch operation.
- **Benefit:** less observer overhead in benchmark logs. This is not evidence of a production FPS improvement with perf disabled.
- **Complexity/risk:** low-medium. Compare perf off/on/census on, verify preserved ordinary counters and identical required memory operations.

## Boundaries and rejected shortcuts

- Native shadow snapshots and host-memory budget caps already exist. Disabling shadow, removing protection/barriers, or importing guest memory directly is not a safe UMA optimization.
- The Linux backend uses userfaultfd asynchronous write protection and PAGEMAP_SCAN (`runtime/platform/linux.c`, write-watch section). The kernel documents that scanning can also re-protect pages; a second scan is not an equivalent independent oracle. See [Linux pagemap documentation](https://www.kernel.org/doc/html/latest/admin-guide/mm/pagemap.html). Runtime feature availability on the user's exact SteamOS kernel remains to be checked.
- FMA/FP improvements already have exact exceptional paths. No new SIMD simplification is recommended from this review; run differential Unicorn tests if lifter/FP work is proposed later.
- ROM reads are already positional. Broad preloading could worsen shared-RAM pressure; no new ROM cache is justified without streaming traces.
- BC3 and host budget handling are already present. Tune neither image quality nor retention defaults from a dedicated-GPU assumption. The 16 GB is shared with guest memory, staging, OS and caches.
- Recorder spin policies and virtual-core preemption are not changed. Their waits are dependencies, not automatically wasted work.

## Reproducible experimental plan

1. Record executable/runtime revision, guest game version (1.2.1 versus 1.4.3), generated-code flags, SteamOS/kernel/Mesa version, configuration and cache state. Baseline is unmodified `7a37fc3`, not Beta 3.
2. Use LCD Deck, 8 W TDP, 1000 MHz GPU reference, same actual resolution/render scale, refresh rate, FPS cap, recorder/replay settings, save, weather/time and route. Keep overlays and logging identical. Allow thermal stabilization and record throttling/clocks rather than assuming fixed CPU frequency.
3. One candidate at a time. Alternate baseline/candidate order (ABBA), at least three repeated captures per condition, fixed duration after warm-up (for example 120 seconds). Include demanding traversal, streaming transition and a static control scene.
4. Separate cold shader/pipeline cache experiments from warm-cache steady state. Use copied independent cache directories; retain the normal installation and saves. Do not erase the user's caches. Log first traversal separately.
5. Capture delivered-frame intervals: average FPS, explicitly defined 1% low (1000 / mean of slowest 1% frame times in ms), median/p95/p99 frametime. Record CPU/GPU time and utilization/frequency, replay/waits, thread CPU time, RAM/RSS and memory budget/pressure, and count/time of the operation being changed. Report variability and outliers, not only the best run.
6. Shadow includes write-watch work; global write-watch includes other consumers. Do not sum overlapping totals. Split prologues may be inside replay while the final prologue is outside. Compare wall-clock critical-path intervals and thread CPU time separately. Existing diagnostics may perturb results; run a perf-disabled control.
7. Functional validation precedes performance acceptance: graphics/state equivalence, uploads/write-backs across fences, scene transitions, suspend/resume as applicable, no new validation errors or missing pipelines. Python models are useful but do not establish actual C/GPU correctness.

## Implementation phases and decision

Phase 0: obtain controlled v0.4.0 baseline, determine actual guest version and enabled paths; quantify diagnostic overhead. N1 can be explored using its existing knob without code changes in a future authorized experiment.

Phase 1: after authorization, one small source change: R1 QUADS direct scratch. Follow with independently reviewable R2 and R3 if measurements support them. Preserve all current contexts and synchronization.

Phase 2: evaluate N1/N2/N3 separately; prioritize N3 for cold-cache stutter and N1 for sustained CPU/power contention. Use the lowest-risk policy that demonstrates a repeatable gain.

Phase 3: only with frequency/cost evidence, investigate N5 and N4; then context-aware binding-offset/ubo_ptr/view recoveries. The stashed Beta 4 patch is reference material, not a patch queue.

Top three research targets: guest polling (N1), background compilation contention (N3), and deferred shadow command aggregation (N5). This is a research priority; the first recommended code modification remains the smaller R1 QUADS recovery. No FPS improvement or native compilation has been demonstrated here.

## Validation performed and instructions review

Read-only source/reference/history inspection and Git state checks. No Python runtime tests, native builds, Deck benchmarks or private game-file operations were needed or executed. Documentation-only whitespace checks are performed at delivery.

Proposed minimal future AGENTS addition: a short fork-workflow section linking agent-context, requiring targeted searches/focused checks and explicit authorization for publication/Linux builds. Do not replace upstream content. Upstream's Windows-first description, PR target `dev` and plain commit sentences differ from this fork's SteamOS scope, branch workflow and Conventional Commits preference; clarify those fork exceptions only with authorization. AGENTS was not edited.
