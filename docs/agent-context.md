# Agent context

## State
- Windows repository, branch `perf/steamos-beta3`, base `30581ed`.
- Started with a clean tree. No commits, push, merges, downloads, C builds, WSL access or AppImage generation.
- Target: Steam Deck LCD / Aerith / RADV, stable performance at 8 W.

## Changes
- `runtime/vk_exec_draw.c`: precompute ordered uniform/storage overlay binding offsets in the existing immutable `RsList` allocation. `bind_sig()` reads current binding values and overlay serials through these offsets instead of repeating range, boundary and alignment filtering per draw. Duplicates and order are preserved; Beta 2 pointer stability rules remain intact.
- `runtime/vk_exec.c`, `runtime/vk_exec.h`: submission-local last temporary view index. Fast hits compare every existing key field plus the blocks usage selector. Registered views and the original fallback lookup retain their behavior. Indices survive reallocations; submission start/completion reset the index. Fence destruction is unchanged.
- `tests/beta3_runtime_test.py`: differential models for mutable bindings, offset bounds/order/duplicates and view keys, growth and submission lifetimes; C source invariants.

## Validation
- 18 Python tests passed: beta3_runtime_test, beta2_runtime_test, dirty_census_test, steamos_runtime_models.
- `git diff --check` passed, including new files checked separately.
- Models/source assertions do not establish native C compilation or GPU correctness. No measured FPS improvement claimed.

## Decisions and next steps
- No texture lock grouping: wider critical sections need concurrency validation beyond lightweight tests.
- No changes to memory/texture coherence paths: existing generation caches and Beta 2 single-pass collection already remove obvious repeated work; further merging could change observable side effects.
- Extra immutable offset storage is bounded by total range bytes / sizeof(p_bind_buf), times four bytes per offset. Last-view misses incur an extra comparison; gains depend on locality.
- Compile incrementally in Ubuntu, then compare Beta 2/Beta 3 on the same Steam Deck scene at 8 W. Exercise BIND_SKIP check mode, changing render targets, block views and multiple submissions; compare frametimes/replay cost and rendering correctness.
