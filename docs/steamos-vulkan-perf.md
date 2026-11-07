# SteamOS Vulkan synchronization diagnostics

Enable TOTK_TRACE=perf in the launch environment. Steam launch options:

    TOTK_TRACE=perf %command%

Keep the validated preset unchanged. Compare identical runs with and without
tracing to quantify overhead on the Deck.

The vk_rec perf lines extend the existing summary every 4096 vk_sync_submit
calls, including empty presentation submissions, not every frame. Nonempty
metrics report calls, total milliseconds, maximum microseconds and disjoint
buckets [0,10), [10,100), [100,1000), [1000,infinity) microseconds. Counters reset
at each summary. Short runs may produce no summary.

- drain_wait: nonempty rec_drain wall time including publication and wakeup.
- ring_full_wait: wait_space slow-path wall time including publication/wakeup.
  The per-packet fast path has no new instrumentation.
- submit_direct / submit_recorder: rec_submit wall time, separated by route.
- recorder_queue: time from the submit packet timestamp before publication to
  submit_run entry, including outstanding recording, scheduling and wake latency.
- queue_lock_direct / queue_lock_recorder: acquisition of vk_queue_lock.
- queue_driver_direct / queue_driver_recorder: duration of vkQueueSubmit2.

Intervals overlap: do not sum them or subtract aggregate maxima. Drain time can
include lock and driver time. Recorder queue time is not pure CPU recording time.
None measures GPU completion. Queue metrics cover rec_submit submissions only,
not independent upload or presentation submissions.

Existing packet, byte, part, fallback and ring-full counters are reused.
Aggregates belong to the producer under the existing submission lock. The
recorder returns one sample through the existing release/acquire tail handoff.
No additional lock or atomic statistic is introduced.

With tracing off there are no new clock reads or histogram updates; the old
unconditional drain clock reads are also skipped. Active clocks are read only
at wait and submit boundaries, never in polling loops or per draw. Logging uses
the existing summary cadence. Actual overhead and SteamOS behavior require
on-device validation; laptop checks do not establish a performance result.
