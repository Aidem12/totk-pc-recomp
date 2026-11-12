# TearsOS Beta 4: diagnostics and feature design

## Executive decision

This extends [the source investigation](steamos-beta4-research.md), not a second optimization audit. Baseline: Windows checkout `main`, `7a37fc3`. The two previous research documents were untracked at entry; they and the Beta 4 stash are preserved. Only this report and agent-context are written. No source changes, captures, builds, installations or device configuration changes.

Three findings change the measurement plan:
1. Runtime `bound` is a heuristic, not a critical-path proof; replay is elapsed time, not thread CPU time. Guest scheduling already exposes useful states and CPU accounting.
2. The always-on `1% low` is derived from an approximate p99 histogram threshold, not the mean of the slowest 1% of frames. It cannot be silently compared with a differently defined external metric.
3. Linux does not have the upstream internal sampling profiler: `prof_win.c` is excluded by `tools/build_game.py:other_os`; `perf.c` supplies a weak stub that logs the absence. Use existing Linux tools if available rather than adding a sampler.

Recommended next phase: establish measurement overhead, capture warm steady-state perf/sched data, then test the existing kernel polling knob. Defer code optimization until the observed critical path supports it.

## Existing instrumentation: semantics, activation and limits

| Mechanism / source | Activation and output | Actual measurement and limits | Cost / use |
|---|---|---|---|
| Frame summary: `perf.c:ft_report` | Always on, every 60 seconds and normal summary/exit path | Mean FPS from frame intervals; counts above 25/50/100 ms; worst frames. The low metric uses the upper edge of a 0.25 ms histogram bin at the slowest 1% threshold. Last bin also contains frames beyond 256 ms: severe tails are not represented exactly. | Histogram/worst-list maintenance per frame, no allocation. First baseline, not exact quantile export. |
| Runtime perf: `perf.c`, `perfmon.h` | `TOTK_TRACE=perf`; main report every 60 presents in this source | Interval mean/max, render-thread wait categories, replay, uploads, shader counts, asynchronous builds and skipped/waited draws. Render busy is derived from interval/accounted waits; it is not OS scheduled CPU time. Nested categories such as ASTC/upload are not additive. | Atomic counters, timing and logs; also enables memory census paths. Compare against trace-off. Do not enable per-draw bind stats by default. |
| GPU timestamps: `vk_sync.c`, `perf.c:gpu_report` | Perf trace or full performance overlay | Timed parts, missed parts, GPU elapsed intervals, inter-part gaps and queued-versus-starved classification. Gaps with work queued can include untimed work or waits. `bound` uses pacing share and GPU/interval thresholds. | Timestamp/query processing; unavailable/partial results must stay marked n/a. Not a hardware-wide GPU utilization or exhaustive dependency trace. |
| Scheduler: `kernel.c:kern_sched_frame` | `TOTK_TRACE=sched`, typically combine `perf,sched`; every 120 presents | Guest states (run/core wait/host/sleep/sync/etc.), CPU time through `plat_thread_cpu_ns`, wake-to-run and core-grant delays, preemption and spin-loop information. Guest state duration is not identical to OS CPU runtime. | Accounting/log overhead; platform CPU-time availability must be checked. Host compiler/driver threads need external sampling for complete coverage. |
| Critical path: kernel tracing + `tools/crit_path.py` | `TOTK_TRACE=sched`, `TOTK_CRIT=w<lo>-<hi>` or frame range, `TOTK_CRIT_OUT=<file>` | Guest state transitions and wake relationships in a bounded interval; useful for heavy-frame causality. Does not trace every OS/driver event or GPU dependency. | High relative overhead, short separate diagnostic run only. Do not use its FPS as the performance verdict. |
| Flight recorder: `flight.c:flight_init`, dump writer | `TOTK_FLIGHT=1`, `TOTK_FLIGHT_THUMBS=0`; select `TOTK_FLIGHT_AT` or existing spike trigger; `TOTK_FLIGHT_DIR` | Bounded ring; dumps `frames.tsv` containing individual interval_ms, blocked buckets and event counts. Default 600 presents is a short window, not a full 120-second benchmark. | Ring memory, per-frame bookkeeping and dump I/O. Thumbnails default on and add GPU blits: disable them for numerical captures. A dump can itself disturb subsequent frames. |
| Pipelines/shaders: `perf.c`, `vk_pipeline.c` stats | Perf trace; existing pipeline summaries | Counts and accumulated build/link timing, blocked render time, skipped and one-shot waits. Cold-cache compilation differs from warm replay. | Existing counters/logs; aggregated timings cannot establish the critical path or a p99 compilation distribution. |
| Memory: `vk_memory.c:shadow_log`, `mirror_log`, census; `perf.c:gpu_report` | Perf trace | Pool/query/copy/write-back totals, shadow deferred copies/reader waits, all-consumer write-watch time, process RAM and budget. | Census may be significant. Shadow includes some write-watch; other consumers also query. Shadow timer placement excludes some guard/lock waiting. Do not add totals or equate budget with physical free RAM. |
| Recorder: `vk_rec.c:rec_stats_log` | Perf trace, periodic caller | Packets/bytes, parts, drain/call counters. | Existing aggregates; not a complete duration breakdown for producer wait, queue mutex and driver submission. Use samples first, not new per-packet logs. |
| Built-in AB: settings `ab`, `kspin_us` | `TOTK_AB=<seconds>` and an explicitly supported `...=ab`; `tools/ab_summary.py` | Alternating A B B A blocks in world time. Only knobs that implement AB participate. | Low switching cost but history, cache and scene evolution confound comparisons. Never use for cold-cache equivalence or across different executables. |
| Windows profiler | `TOTK_PROFILE`, `tools/prof_report.py` | `prof_win.c:prof_sampler_start` suspends/unwinds threads; Linux uses the unavailable stub. | Not a usable SteamOS capture route in this tree. Do not promise it works based on settings documentation alone. |

Existing offline parsers: `python tools/perf_summary.py RUN.log FIRST_FRAME LAST_FRAME`, `python tools/ab_summary.py RUN.log --skip 10`, `python tools/crit_path.py RUN.crit --top 20`. Check parser assumptions before comparing versions; these commands have not been executed against user captures here.

### Distinguishing execution, waiting and GPU starvation

- Useful CPU versus polling: combine per-thread scheduled CPU time with sampled stacks. Samples in `kspin`, `rec_drain` or `wait_until` identify polling work, but samples are not invocation counts.
- Blocked versus runnable but unscheduled: runtime state/wake data explains guest-level causes; Linux scheduler events distinguish sleeping from run-queue delay. A wall-clock timer around a mutex includes both.
- GPU busy versus dependency gaps: runtime timestamps identify its timed work; correlate queued/starved gaps with the CPU timeline. Use RADV traces only if these aggregates leave a material GPU question.
- Gameplay compilation: correlate pipeline wait/async/skip counters, compiler stacks and cold/warm cache state. A background build duration cannot simply be added to frame time.
- Outside replay: inspect `vk_sync_submit` boundaries (dirty census before the replay timer; final upload/prologue after it; split prologues can occur within replay). OS stack samples locate cost without assuming `render busy - replay` is a meaningful exclusive duration.
- Candidate frequency: existing counters cover queries/copies/builds/uploads. Exact `dict_for` or QUADS invocation counts are not generally exported. Sampling estimates CPU share, not calls; short targeted probes may be designed later if symbols/permissions permit, otherwise a minimal counter requires separate implementation authorization.

## SteamOS tools: conditional availability

| Tool | Information | Overhead / ease | Requirements and limitations |
|---|---|---|---|
| Internal logs and TSV | Runtime boundaries, intervals, event counts | Low to high by mode; easiest | No extra installation. Verify actual Linux log output and trace overhead. |
| MangoHud | FPS/frame timing plus CPU/GPU clocks, load, temperatures, RAM/VRAM where supported | Usually convenient; layer, sensor polling and logging are not free | Confirm installed version and Gamescope integration. Steam's visible overlay alone does not prove CSV logging availability. Its configured logging interval may aggregate frames; validate schema and cadence before quantiles. [Official configuration](https://github.com/flightlessmango/MangoHud/blob/master/data/MangoHud.conf). |
| `perf stat` / `perf record` | Task CPU time/counters and sampled stacks | Low for a few counters, higher for DWARF stacks | Tool/kernel support, perf_event_paranoid/capabilities, matching symbols and unwind information. Check permitted events first; no blanket sysctl or root changes. [perf record manual](https://man7.org/linux/man-pages/man1/perf-record.1.html). |
| `perf sched timehist` | Scheduled runtime, runnable delay, off-CPU intervals | Short trace; larger overhead and files | Scheduler tracepoints commonly need additional privileges. Filter target PID/TIDs; avoid whole-system capture unless necessary. [perf sched manual](https://man7.org/linux/man-pages/man1/perf-sched.1.html). |
| `/proc/PID/task`, `pidstat` if installed | Per-thread CPU, RSS and context-switch trends | Low-rate sampling is comparatively light | Per-thread ticks are coarse for short waits; pidstat is not guaranteed present. RSS alone misses total UMA pressure. |
| RADV trace / RGP | GPU event timeline and detailed workload investigation | Intrusive, single-frame diagnosis | Version-dependent Mesa tracing support, compatible hardware/tooling and sufficient capture memory. Current Mesa documents `MESA_VK_TRACE=rgp`; installed SteamOS may differ. [Mesa documentation](https://docs.mesa3d.org/envvars.html). Do not prescribe latest variable names blindly. |
| Vulkan validation / frame capture | Correctness, resource/command inspection | High; exclude from FPS comparisons | Optional layers/tools not assumed installed. GPU readback/serialization can distort normal timing. |

No tools were installed, probed on the Deck or used to capture gameplay in this task.

## Minimal-impact measurement protocol

Use the fixed-scene, thermal, ABBA and cold/warm controls from the research report. Add these requirements:

1. **Inventory, read-only, on authorization:** SteamOS/kernel/Mesa versions; game version; executable hash; effective ini/environment; current cache identities; presence/version of perf and MangoHud; read perf_event_paranoid; available symbols; verify frame timestamps and Linux thread accounting. No settings changes needed for inventory.
2. **Tier 0:** default frame summary, perf off, no capture layers, fixed save and route. Then repeat with the chosen lightweight external frame logger to estimate its impact. Preserve normal caches/saves.
3. **Tier 1:** separate warm run with `TOTK_TRACE=perf,sched`; record a common world-time and frame-number interval. Compare with Tier 0 to quantify observer cost. Capture frequency/load/temperature/RAM trends with one tool, not overlapping overlays.
4. **Exact intervals:** obtain validated per-frame data from a supported logger or flight TSV. For a complete 120-second flight capture, explicitly size the ring above the maximum presents and trigger a dump at its end; disable thumbnails and exclude dump-time frames. Compute median/p95/p99 from intervals; define mean-tail 1% low separately from upstream's histogram threshold. Do not derive quantiles from 60-frame means or 100 ms CSV averages.
5. **Tier 2:** choose one short diagnostic based on Tier 1: critical path for guest waits; `perf record` for CPU stacks; scheduler trace for run-queue delay; RADV capture for unresolved GPU gaps. Never enable all at once. Symbolized CPU samples need the matching binary/debug artifacts; inlined/tail-called functions complicate attribution.
6. **First experiment:** warm static scene, polling default versus zero using the existing knob; same cap/recorder/replay. AB mode can screen it, but confirm through repeated separate runs. Accept only repeatable tail/energy benefit without wake-latency regression.
7. **Second experiment:** repeat a controlled traversal with comparable cold versus warm caches, measuring background builds, render waits/skips, CPU frequency and guest core delays. Diagnose compilation competition before selecting a worker policy. Do not change several worker pools together.
8. **Memory experiment only if indicated:** inspect shadow query/copy/deferred and reader-wait counts alongside CPU samples. Quantify opportunities for command aggregation without deleting synchronization or summing overlapping costs.

Report all runs, variation and rejected samples with reasons. Beta 3 is historical only unless game version, route, config, graphics output and cache preparation are comparable. Its different runtime architecture prevents causal attribution of a whole-build FPS difference to one patch.

## Upstream functionality inventory (available in v0.4.0, not necessarily all introduced by this release)

| Capability | Verified source | Classification / TearsOS implication |
|---|---|---|
| Frame-tail summary, full performance overlay, flight recorder and critical path | `perf.c`, `flight.c`, `ui/menu.cpp`, kernel tracing | Already available. Extend access/export, not a second telemetry backend. |
| AB blocks and summary tools | `cfg.c`, `kernel.c`, `tools/ab_summary.py` | Available for selected switches; experiment identity/cross-build comparability is only partial. |
| Persistent settings and reset confirmation | `cfg.c`, `ui/menu.cpp:reset_all` | Already available. A versioned known-good snapshot/restore is distinct from resetting defaults. |
| FSR choice, color presets, graphics controls; cheats/rebuild and amiibo UI | `ui/menu.cpp`, `ui/cheats_tab.cpp`, `ui/amiibo_page.cpp` | Visible capabilities already exist. No need to add generic settings or an amiibo page. HDR presence does not imply usefulness on the LCD panel. |
| Shader cache paths, translator identity and packed prepared cache | `docs/settings.md`, `installer/steps.py` prepare path | Cache preparation/invalidation exists; user-facing cache identity/space reporting and safe experiment isolation are potential extensions. |
| Native guest 1.4.3 decode and game-version support | Previous research, `ov_zstd.c` | Existing capability; do not treat it as a new TearsOS feature or apply patch addresses to 1.2.1. |

## Proposed TearsOS features (design only)

| Feature / category | Need and upstream gap | Design and affected area | Benefit; complexity/risk | Validation / recommendation |
|---|---|---|---|---|
| **Experiment manifest and comparable-run report** — new diagnostic/maintenance capability | Existing AB/log parsers do not establish all hardware/build/cache/config equivalence or normalize different low-FPS definitions | Small offline wrapper around existing parsers; record revision/game/toolchain/kernel/Mesa/config hash, cache mode, route/time window, metric definitions and capture mode. Reject or visibly mark incompatible comparisons; no new per-frame telemetry | Reliable regression decisions; low-medium/low. No resident work | Fixtures for changed cap/game version, missing frames, mixed clocks, truncated logs and cold/warm mismatch. **Implement after authorization**, first feature priority |
| **Known-good preference snapshots and cache identity panel** — user functionality | Reset-to-default and cache preparation exist; they do not provide user-selected named rollback with compatibility review | Extend current cfg/menu or installer surface: save whitelisted preferences with schema/build metadata; preview diff and restart-required options on restore. Show translator/cache identity and disk size; no automatic cache deletion, no secrets/game data | Recoverable experimentation and fewer accidental cold runs; medium/medium (config migration, interrupted writes, disk scans) | Unknown/removed keys, atomic failure, environment overrides, interrupted restore and preserved saves. **Implement in a narrow preferences-first phase**; cache management remains read-only initially |
| **On-demand Deck diagnostic bundle** — user-accessible instrumentation feature | Logs/flight dumps exist but collecting relevant material is manual and easy to overcapture | Bounded activation using existing perf/sched/flight with thumbnails off; export effective settings, hardware summary and selected logs to a local folder; show active capture and stop/dump status. Avoid trying to change startup-only options live without existing support; restart-required capture is acceptable | Reproducible support and measurements; medium/medium due to observer cost and private paths | Expiry/stop, disk-full, trace restoration, absent GPU timestamps and path redaction. No automatic upload or memory/game-file dumps. **Prototype after manifest format is stable**, reuse existing recorder |
| Deck background-job policy — performance functionality | Multiple existing worker pools, no proven coordinated 8 W optimum | Limited policy using measured workload/phase, retaining demanded-job priority and fallback; avoid a global scheduler | Potential cold-cache tail reduction; medium-high/high | One-shot rendering, queue latency, starvation and cold/warm repetitions. **Investigate**, not ready to implement |

The first three add capabilities rather than merely renaming existing switches. A generic new overlay, new sampler, automatic optimizer, universal cache purge, or an unconditional 'more workers' mode is rejected: duplication or unjustified risk. Do not claim presets are optimal before device testing.

## Separate priorities and relationships

**A — Internal performance:** (1) establish critical path and evaluate existing polling policy; (2) recover QUADS direct scratch as the smallest code candidate if its frequency justifies it; (3) assess compilation contention for cold-cache tails; (4) WbHits/optional diagnostic census; (5) deferred-copy aggregation only with proof and measurable command frequency. zstd lookup stays low priority until a 1.4.3 streaming profile implicates it. This refines, rather than repeats, the prior report.

**B — New features:** (1) comparable-run manifest/report; (2) known-good preferences; (3) bounded diagnostic bundle. Manifest validation protects every optimization decision; preference rollback makes AB experimentation recoverable; the bundle supplies existing evidence without permanent collection.

## Pending data, risks and next authorized phase

Need actual SteamOS/kernel/Mesa, game version, effective config and caps, binary/symbol availability, installed/permitted tools, representative perf/sched logs, per-frame capture schema and cold/warm cache state. No local evidence establishes function frequency, polling waste, compiler contention or GPU bottleneck severity on this Deck.

Next request should authorize a read-only Deck inventory and one warm-scene measurement protocol, not a runtime patch. All commands above describe future use; none were run on the device. No new tests are required for runtime behavior because only documentation changed. Whitespace/status checks at delivery verify the document scope; they do not validate proposed tools on SteamOS.
