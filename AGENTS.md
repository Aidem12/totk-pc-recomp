## Project Context

This repository is a fork of `valentderah/totk-pc-recomp`, focused on optimizing Linux/SteamOS performance for Steam Deck LCD.

Primary target:
- Steam Deck LCD (AMD Aerith, Zen 2, RDNA 2).
- Vulkan / RADV on SteamOS.
- 8 W TDP efficiency target.
- Reduce CPU overhead, memory operations, and frame-time spikes.
- Preserve correctness and upstream compatibility.

## Agent Behavior

- Be concise, technical, and task-focused.
- Avoid repeating information already established.
- Prefer actions and results over lengthy explanations.
- Do not provide generic tutorials unless requested.
- Do not restate the entire task before starting.
- Ask questions only when essential information is missing.
- Complete the requested task without unrelated improvements.
- Use existing project conventions.

## Token Efficiency

- Search for relevant symbols and functions before opening files.
- Read only the necessary files and line ranges.
- Prefer `rg`, `git diff`, and targeted searches.
- Avoid dumping large files, logs, or directory listings.
- Limit command output to relevant matches or summaries.
- Reuse findings from the current session.
- Do not repeatedly inspect unchanged files.
- Avoid unnecessary documentation searches.
- Summarize findings instead of copying large code sections.
- Keep internal working notes short and factual.
- Do not create additional reports unless requested.

## Code Changes

- Make the smallest correct change.
- Preserve existing behavior unless explicitly requested.
- Avoid unrelated refactoring and formatting changes.
- Do not introduce dependencies without justification.
- Reuse existing helpers when appropriate.
- Do not optimize code without identifying the actual overhead.
- Preserve Vulkan synchronization, resource lifetimes, and memory coherence.
- Prioritize correctness over speculative performance gains.

## Performance Optimization

- Focus on measured CPU/runtime bottlenecks.
- Consider Steam Deck's shared CPU/GPU power budget.
- Avoid assuming discrete GPU behavior applies to UMA.
- Avoid unnecessary allocations, repeated scans, and redundant work.
- Do not assume an optimization improves FPS without benchmarks.
- Separate measured gains from theoretical improvements.
- Keep CPU/GPU work ordering and synchronization correct.
- Prefer reversible, isolated optimizations.

## Testing

- Run focused tests related to changed code first.
- Expand test coverage only when necessary.
- Do not rebuild the entire project for documentation-only changes.
- Prefer incremental builds when possible.
- Report failed checks and relevant warnings concisely.
- Never claim tests passed unless executed.
- Do not suppress errors to make tests pass.
- Do not modify unrelated code to fix pre-existing failures.

## Git Safety

- Check the current branch and working tree before editing.
- Preserve existing uncommitted changes.
- Never run destructive Git commands without approval.
- Do not commit, push, merge, rebase, or create releases unless requested.
- Never modify the upstream repository directly.
- Follow Conventional Commits when commits are requested.
- Do not include generated game data or copyrighted assets.

## Build Safety

- Reuse existing toolchains, dependencies, and build artifacts.
- Avoid unnecessary full rebuilds.
- Use isolated build directories for experiments.
- Preserve existing releases and working builds.
- Do not delete caches unless explicitly required.
- Never assume a successful runtime build means the full game is validated.

## Response Format

Use short responses with:

1. Result or finding.
2. Files changed, if applicable.
3. Tests executed and their results.
4. Remaining issues or next step.

Avoid long introductions, repeated summaries, and unnecessary suggestions.

## Priority

Correctness > Safety > Minimal Changes > Performance > Token Efficiency

When additional investigation is necessary for correctness, perform it rather than sacrificing quality to save tokens.
