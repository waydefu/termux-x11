#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * P2-B.3a telemetry is deliberately a bounded shared-memory ring.  The X
 * server is the producer of records; the renderer and buffer helpers fill
 * timing fields for the record index carried by a GPU queue entry or the
 * process-local current context. No per-pixel logging and no fsync are done
 * on the hot path.
 */
#define LORIE_B3A_SCHEMA_VERSION 2u
#define LORIE_B3A_MAX_RECORDS 16384u
#define LORIE_B3A_INVALID_INDEX UINT32_MAX

enum LorieB3aCandidate {
    LORIE_B3A_CANDIDATE_UNKNOWN = 0,
    LORIE_B3A_CANDIDATE_CPU = 1,
    LORIE_B3A_CANDIDATE_R3 = 2,
};

enum LorieB3aPhase {
    LORIE_B3A_VALID_PREPARE = 1ull << 0,
    LORIE_B3A_VALID_PROMOTION = 1ull << 1,
    LORIE_B3A_VALID_CLONE = 1ull << 2,
    LORIE_B3A_VALID_UPLOAD = 1ull << 3,
    LORIE_B3A_VALID_QUEUE = 1ull << 4,
    LORIE_B3A_VALID_DRAW_SUBMIT = 1ull << 5,
    LORIE_B3A_VALID_FENCE = 1ull << 6,
    LORIE_B3A_VALID_DONE = 1ull << 7,
    LORIE_B3A_VALID_REPAIR = 1ull << 8,
    LORIE_B3A_VALID_WALL = 1ull << 9,
    LORIE_B3A_VALID_SERIAL = 1ull << 10,
    LORIE_B3A_VALID_BATCH = 1ull << 11,
    LORIE_B3A_VALID_RECT = 1ull << 12,
    LORIE_B3A_VALID_FALLBACK = 1ull << 13,
    LORIE_B3A_VALID_AHB_ALLOC = 1ull << 14,
    LORIE_B3A_VALID_AHB_LOCK = 1ull << 15,
    LORIE_B3A_VALID_BUFFER_COPY = 1ull << 16,
    LORIE_B3A_VALID_AHB_UNLOCK = 1ull << 17,
    LORIE_B3A_VALID_FD_ALLOC = 1ull << 18,
    LORIE_B3A_VALID_FD_MMAP = 1ull << 19,
    LORIE_B3A_VALID_CLONE_COPY = 1ull << 20,
    LORIE_B3A_VALID_CORRECTNESS = 1ull << 21,
    LORIE_B3A_VALID_STAGING = 1ull << 22,
};

/* The layout intentionally mirrors the JSON/CSV record documented by Gate B3a. */
typedef struct {
    uint64_t sequence;
    uint8_t candidate;
    uint8_t op;
    uint8_t fallback;
    uint8_t reserved;
    uint64_t serial;
    uint32_t src_format;
    uint32_t mask_format;
    uint32_t dst_format;

    int32_t rect_w, rect_h;
    int32_t src_w, src_h, src_stride;
    int32_t dst_w, dst_h, dst_stride;
    uint64_t rect_area, src_area;
    double src_rect_ratio;
    uint32_t batch_rects;

    uint64_t prepare_ns;
    uint64_t promotion_ns;
    uint64_t ahb_allocate_ns;
    uint64_t ahb_lock_ns;
    uint64_t buffer_copy_ns;
    uint64_t ahb_unlock_ns;
    uint64_t fd_allocate_ns;
    uint64_t fd_mmap_ns;
    uint64_t clone_bytes;
    uint64_t clone_logical_bytes;
    uint64_t clone_physical_bytes;
    uint64_t clone_ns;
    uint64_t clone_copy_ns;
    uint64_t upload_bytes;
    uint64_t upload_logical_bytes;
    uint64_t upload_physical_bytes;
    uint64_t upload_ns;
    uint64_t queue_ns;
    uint64_t draw_submit_ns;
    uint64_t gpu_exec_ns;
    uint64_t fence_wait_ns;
    uint64_t done_composite_ns;
    uint64_t repair_bytes;
    uint64_t repair_ns;
    uint64_t wall_ns;

    /* These are intentionally invalid unless an external oracle populates them. */
    uint64_t exact_fail;
    uint64_t max_delta;
    uint64_t x_nz;

    /* D0a persistent-staging observability. hit/miss are 0/1 per record;
     * buffer_id is the reused FD staging allocation id (0 when D0a is off). */
    uint64_t staging_cache_hit;
    uint64_t staging_cache_miss;
    uint64_t staging_buffer_id;

    /* Process-local timestamps used to derive queue latency and wall time. */
    uint64_t start_ns;
    uint64_t queue_publish_ns;

    volatile uint64_t valid_mask;
} LorieB3aRecord;

typedef struct {
    volatile uint64_t next_record;
    volatile uint64_t dropped_records;
    volatile uint32_t dumped;
    uint32_t schema_version;
    uint64_t x_rss_start_bytes;
    uint64_t x_rss_end_bytes;
    uint64_t x_fd_start;
    uint64_t x_fd_end;
    uint64_t renderer_rss_start_bytes;
    uint64_t renderer_rss_end_bytes;
    uint64_t renderer_fd_start;
    uint64_t renderer_fd_end;
    LorieB3aRecord records[LORIE_B3A_MAX_RECORDS];
} LorieB3aTelemetry;

struct lorie_shared_server_state;

bool lorieB3aEnabled(void);
uint8_t lorieB3aCandidateFromEnv(void);
uint64_t lorieB3aNowNs(void);

uint32_t lorieB3aBegin(LorieB3aTelemetry *telemetry, uint8_t candidate, uint8_t op,
                       uint32_t src_format, uint32_t mask_format, uint32_t dst_format,
                       int32_t rect_w, int32_t rect_h);
void lorieB3aSetGeometry(LorieB3aTelemetry *telemetry, uint32_t index,
                         int32_t src_w, int32_t src_h, int32_t src_stride,
                         int32_t dst_w, int32_t dst_h, int32_t dst_stride,
                         int32_t rect_w, int32_t rect_h);
void lorieB3aSetBatch(LorieB3aTelemetry *telemetry, uint32_t index, uint32_t batch_rects);
void lorieB3aSetSerial(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t serial);
void lorieB3aSetFallback(LorieB3aTelemetry *telemetry, uint32_t index, bool fallback);
void lorieB3aAddNs(LorieB3aTelemetry *telemetry, uint32_t index, enum LorieB3aPhase phase, uint64_t ns);
/* Buffer code uses this process-local context to attribute allocation/lock/copy
 * phases without coupling LorieBuffer APIs to the X server state structure. */
void lorieB3aSetCurrent(LorieB3aTelemetry *telemetry, uint32_t index);
void lorieB3aClearCurrent(void);
void lorieB3aRecordCurrentPhase(enum LorieB3aPhase phase, uint64_t ns);
void lorieB3aAddCloneBytes(LorieB3aTelemetry *telemetry, uint32_t index,
                           uint64_t logical_bytes, uint64_t physical_bytes);
void lorieB3aAddUploadBytes(LorieB3aTelemetry *telemetry, uint32_t index,
                            uint64_t logical_bytes, uint64_t physical_bytes);
void lorieB3aMarkQueuePublished(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t published_ns);
void lorieB3aMarkQueueDequeued(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t dequeued_ns);
void lorieB3aFinish(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t wall_ns);
void lorieB3aMarkRepair(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t bytes, uint64_t ns);
void lorieB3aMarkCorrectness(LorieB3aTelemetry *telemetry, uint32_t index,
                             uint64_t exact_fail, uint64_t max_delta, uint64_t x_nz);
void lorieB3aMarkStaging(LorieB3aTelemetry *telemetry, uint32_t index,
                         bool hit, uint64_t buffer_id);

/* Writes <base>.json and <base>.csv once when TERMUX_X11_B3A_OUTPUT is set. */
void lorieB3aDump(LorieB3aTelemetry *telemetry, const char *reason);

/* Process metadata is sampled only at lifecycle boundaries, never per operation. */
void lorieB3aCaptureProcessStart(LorieB3aTelemetry *telemetry, bool renderer);
void lorieB3aCaptureProcessEnd(LorieB3aTelemetry *telemetry, bool renderer);

#ifdef __cplusplus
}
#endif
