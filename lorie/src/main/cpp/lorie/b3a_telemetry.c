#include "b3a_telemetry.h"

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int b3a_enabled = -1;
static __thread LorieB3aTelemetry *b3a_current_telemetry;
static __thread uint32_t b3a_current_index = LORIE_B3A_INVALID_INDEX;

bool lorieB3aEnabled(void) {
    const char *value;
    if (b3a_enabled >= 0)
        return b3a_enabled != 0;
    value = getenv("TERMUX_X11_B3A_TELEMETRY");
    b3a_enabled = value && value[0] && strcmp(value, "0") != 0;
    return b3a_enabled != 0;
}

uint8_t lorieB3aCandidateFromEnv(void) {
    const char *value = getenv("TERMUX_X11_B3A_CANDIDATE");
    if (!value)
        return LORIE_B3A_CANDIDATE_UNKNOWN;
    if (!strcmp(value, "cpu"))
        return LORIE_B3A_CANDIDATE_CPU;
    if (!strcmp(value, "r3"))
        return LORIE_B3A_CANDIDATE_R3;
    return LORIE_B3A_CANDIDATE_UNKNOWN;
}

uint64_t lorieB3aNowNs(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return 0;
    return (uint64_t) ts.tv_sec * 1000000000ull + (uint64_t) ts.tv_nsec;
}

static LorieB3aRecord *b3a_record(LorieB3aTelemetry *telemetry, uint32_t index) {
    if (!telemetry || index == LORIE_B3A_INVALID_INDEX || index >= LORIE_B3A_MAX_RECORDS)
        return NULL;
    return &telemetry->records[index];
}

static void b3a_valid(LorieB3aRecord *record, uint64_t bit) {
    if (record)
        __sync_fetch_and_or(&record->valid_mask, bit);
}

static uint64_t b3a_rss_bytes(void) {
    FILE *fp = fopen("/proc/self/status", "r");
    char line[256];
    unsigned long long value;
    if (!fp)
        return 0;
    while (fgets(line, sizeof(line), fp)) {
        if (sscanf(line, "VmRSS: %llu kB", &value) == 1) {
            fclose(fp);
            return (uint64_t) value * 1024ull;
        }
    }
    fclose(fp);
    return 0;
}

static uint64_t b3a_fd_count(void) {
    DIR *dir = opendir("/proc/self/fd");
    struct dirent *entry;
    uint64_t count = 0;
    if (!dir)
        return 0;
    while ((entry = readdir(dir)) != NULL) {
        if (entry->d_name[0] != '.')
            count++;
    }
    closedir(dir);
    return count;
}

void lorieB3aCaptureProcessStart(LorieB3aTelemetry *telemetry, bool renderer) {
    uint64_t rss, fds;
    if (!lorieB3aEnabled() || !telemetry)
        return;
    rss = b3a_rss_bytes();
    fds = b3a_fd_count();
    if (renderer) {
        telemetry->renderer_rss_start_bytes = rss;
        telemetry->renderer_fd_start = fds;
    } else {
        telemetry->x_rss_start_bytes = rss;
        telemetry->x_fd_start = fds;
    }
}

void lorieB3aCaptureProcessEnd(LorieB3aTelemetry *telemetry, bool renderer) {
    uint64_t rss, fds;
    if (!lorieB3aEnabled() || !telemetry)
        return;
    rss = b3a_rss_bytes();
    fds = b3a_fd_count();
    if (renderer) {
        telemetry->renderer_rss_end_bytes = rss;
        telemetry->renderer_fd_end = fds;
    } else {
        telemetry->x_rss_end_bytes = rss;
        telemetry->x_fd_end = fds;
    }
}

uint32_t lorieB3aBegin(LorieB3aTelemetry *telemetry, uint8_t candidate, uint8_t op,
                       uint32_t src_format, uint32_t mask_format, uint32_t dst_format,
                       int32_t rect_w, int32_t rect_h) {
    uint64_t sequence;
    uint32_t index;
    LorieB3aRecord *record;
    if (!lorieB3aEnabled() || !telemetry)
        return LORIE_B3A_INVALID_INDEX;
    sequence = __sync_fetch_and_add(&telemetry->next_record, 1);
    if (sequence >= LORIE_B3A_MAX_RECORDS) {
        __sync_fetch_and_add(&telemetry->dropped_records, 1);
        return LORIE_B3A_INVALID_INDEX;
    }
    index = (uint32_t) sequence;
    record = &telemetry->records[index];
    memset(record, 0, sizeof(*record));
    record->sequence = sequence + 1;
    record->candidate = candidate;
    record->op = op;
    record->src_format = src_format;
    record->mask_format = mask_format;
    record->dst_format = dst_format;
    record->rect_w = rect_w;
    record->rect_h = rect_h;
    record->start_ns = lorieB3aNowNs();
    if (rect_w > 0 && rect_h > 0) {
        record->rect_area = (uint64_t) rect_w * (uint64_t) rect_h;
        b3a_valid(record, LORIE_B3A_VALID_RECT);
    }
    return index;
}

void lorieB3aSetGeometry(LorieB3aTelemetry *telemetry, uint32_t index,
                         int32_t src_w, int32_t src_h, int32_t src_stride,
                         int32_t dst_w, int32_t dst_h, int32_t dst_stride,
                         int32_t rect_w, int32_t rect_h) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->src_w = src_w;
    record->src_h = src_h;
    record->src_stride = src_stride;
    record->dst_w = dst_w;
    record->dst_h = dst_h;
    record->dst_stride = dst_stride;
    if (rect_w > 0 && rect_h > 0) {
        record->rect_w = rect_w;
        record->rect_h = rect_h;
        record->rect_area = (uint64_t) rect_w * (uint64_t) rect_h;
    }
    if (src_w > 0 && src_h > 0) {
        record->src_area = (uint64_t) src_w * (uint64_t) src_h;
        if (record->rect_area) {
            record->src_rect_ratio = (double) record->src_area / (double) record->rect_area;
            b3a_valid(record, LORIE_B3A_VALID_RECT);
        }
    }
}

void lorieB3aSetBatch(LorieB3aTelemetry *telemetry, uint32_t index, uint32_t batch_rects) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->batch_rects = batch_rects;
    b3a_valid(record, LORIE_B3A_VALID_BATCH);
}

void lorieB3aSetSerial(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t serial) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->serial = serial;
    b3a_valid(record, LORIE_B3A_VALID_SERIAL);
}

void lorieB3aSetFallback(LorieB3aTelemetry *telemetry, uint32_t index, bool fallback) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->fallback = fallback ? 1 : 0;
    b3a_valid(record, LORIE_B3A_VALID_FALLBACK);
}

void lorieB3aAddNs(LorieB3aTelemetry *telemetry, uint32_t index, enum LorieB3aPhase phase, uint64_t ns) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    uint64_t *value = NULL;
    if (!record)
        return;
    switch (phase) {
        case LORIE_B3A_VALID_PREPARE: value = &record->prepare_ns; break;
        case LORIE_B3A_VALID_PROMOTION: value = &record->promotion_ns; break;
        case LORIE_B3A_VALID_CLONE: value = &record->clone_ns; break;
        case LORIE_B3A_VALID_UPLOAD: value = &record->upload_ns; break;
        case LORIE_B3A_VALID_QUEUE: value = &record->queue_ns; break;
        case LORIE_B3A_VALID_DRAW_SUBMIT: value = &record->draw_submit_ns; break;
        case LORIE_B3A_VALID_FENCE: value = &record->fence_wait_ns; break;
        case LORIE_B3A_VALID_DONE: value = &record->done_composite_ns; break;
        case LORIE_B3A_VALID_REPAIR: value = &record->repair_ns; break;
        case LORIE_B3A_VALID_AHB_ALLOC: value = &record->ahb_allocate_ns; break;
        case LORIE_B3A_VALID_AHB_LOCK: value = &record->ahb_lock_ns; break;
        case LORIE_B3A_VALID_BUFFER_COPY: value = &record->buffer_copy_ns; break;
        case LORIE_B3A_VALID_AHB_UNLOCK: value = &record->ahb_unlock_ns; break;
        case LORIE_B3A_VALID_FD_ALLOC: value = &record->fd_allocate_ns; break;
        case LORIE_B3A_VALID_FD_MMAP: value = &record->fd_mmap_ns; break;
        case LORIE_B3A_VALID_CLONE_COPY: value = &record->clone_copy_ns; break;
        default: return;
    }
    *value += ns;
    b3a_valid(record, (uint64_t) phase);
}

void lorieB3aSetCurrent(LorieB3aTelemetry *telemetry, uint32_t index) {
    if (!lorieB3aEnabled())
        return;
    b3a_current_telemetry = telemetry;
    b3a_current_index = index;
}

void lorieB3aClearCurrent(void) {
    b3a_current_telemetry = NULL;
    b3a_current_index = LORIE_B3A_INVALID_INDEX;
}

void lorieB3aRecordCurrentPhase(enum LorieB3aPhase phase, uint64_t ns) {
    if (!b3a_current_telemetry || b3a_current_index == LORIE_B3A_INVALID_INDEX)
        return;
    lorieB3aAddNs(b3a_current_telemetry, b3a_current_index, phase, ns);
}

void lorieB3aAddCloneBytes(LorieB3aTelemetry *telemetry, uint32_t index,
                           uint64_t logical_bytes, uint64_t physical_bytes) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->clone_logical_bytes += logical_bytes;
    record->clone_physical_bytes += physical_bytes;
    record->clone_bytes += logical_bytes;
    b3a_valid(record, LORIE_B3A_VALID_CLONE);
}

void lorieB3aAddUploadBytes(LorieB3aTelemetry *telemetry, uint32_t index,
                            uint64_t logical_bytes, uint64_t physical_bytes) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->upload_logical_bytes += logical_bytes;
    record->upload_physical_bytes += physical_bytes;
    record->upload_bytes += physical_bytes;
    b3a_valid(record, LORIE_B3A_VALID_UPLOAD);
}

void lorieB3aMarkQueuePublished(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t published_ns) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->queue_publish_ns = published_ns;
}

void lorieB3aMarkQueueDequeued(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t dequeued_ns) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record || !record->queue_publish_ns || dequeued_ns < record->queue_publish_ns)
        return;
    record->queue_ns += dequeued_ns - record->queue_publish_ns;
    b3a_valid(record, LORIE_B3A_VALID_QUEUE);
}

void lorieB3aFinish(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t wall_ns) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->wall_ns = wall_ns;
    b3a_valid(record, LORIE_B3A_VALID_WALL);
}

void lorieB3aMarkRepair(LorieB3aTelemetry *telemetry, uint32_t index, uint64_t bytes, uint64_t ns) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->repair_bytes += bytes;
    record->repair_ns += ns;
    b3a_valid(record, LORIE_B3A_VALID_REPAIR);
}

void lorieB3aMarkCorrectness(LorieB3aTelemetry *telemetry, uint32_t index,
                             uint64_t exact_fail, uint64_t max_delta, uint64_t x_nz) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    record->exact_fail = exact_fail;
    record->max_delta = max_delta;
    record->x_nz = x_nz;
    b3a_valid(record, LORIE_B3A_VALID_CORRECTNESS);
}

void lorieB3aMarkStaging(LorieB3aTelemetry *telemetry, uint32_t index,
                         bool hit, uint64_t buffer_id) {
    LorieB3aRecord *record = b3a_record(telemetry, index);
    if (!record)
        return;
    if (hit)
        record->staging_cache_hit = 1;
    else
        record->staging_cache_miss = 1;
    record->staging_buffer_id = buffer_id;
    b3a_valid(record, LORIE_B3A_VALID_STAGING);
}

static const char *b3a_candidate_name(uint8_t candidate) {
    switch (candidate) {
        case LORIE_B3A_CANDIDATE_CPU: return "cpu";
        case LORIE_B3A_CANDIDATE_R3: return "r3";
        default: return "unknown";
    }
}

static void json_u64(FILE *fp, const LorieB3aRecord *record, uint64_t bit, uint64_t value) {
    if (record->valid_mask & bit)
        fprintf(fp, "%llu", (unsigned long long) value);
    else
        fputs("null", fp);
}

static void json_i32(FILE *fp, const LorieB3aRecord *record, uint64_t bit, int32_t value) {
    if (record->valid_mask & bit)
        fprintf(fp, "%d", value);
    else
        fputs("null", fp);
}

static void json_positive_i32(FILE *fp, int32_t value) {
    if (value > 0)
        fprintf(fp, "%d", value);
    else
        fputs("null", fp);
}

static void csv_positive_i32(FILE *fp, int32_t value) {
    if (value > 0)
        fprintf(fp, "%d", value);
    else
        fputs("NA", fp);
}

static void json_ratio(FILE *fp, const LorieB3aRecord *record, uint64_t bit,
                       uint64_t numerator, uint64_t denominator) {
    if ((record->valid_mask & bit) && denominator)
        fprintf(fp, "%.6f", (double) numerator / (double) denominator);
    else
        fputs("null", fp);
}

static void csv_ratio(FILE *fp, const LorieB3aRecord *record, uint64_t bit,
                      uint64_t numerator, uint64_t denominator) {
    if ((record->valid_mask & bit) && denominator)
        fprintf(fp, "%.6f", (double) numerator / (double) denominator);
    else
        fputs("NA", fp);
}

static void json_record(FILE *fp, const LorieB3aRecord *record) {
    uint64_t rect_bytes = record->rect_area * 4ull;
    fprintf(fp, "{\"candidate\":\"%s\",\"serial\":", b3a_candidate_name(record->candidate));
    json_u64(fp, record, LORIE_B3A_VALID_SERIAL, record->serial);
    fprintf(fp, ",\"rect_w\":"); json_i32(fp, record, LORIE_B3A_VALID_RECT, record->rect_w);
    fprintf(fp, ",\"rect_h\":"); json_i32(fp, record, LORIE_B3A_VALID_RECT, record->rect_h);
    fputs(",\"src_w\":", fp); json_positive_i32(fp, record->src_w);
    fputs(",\"src_h\":", fp); json_positive_i32(fp, record->src_h);
    fputs(",\"src_stride\":", fp); json_positive_i32(fp, record->src_stride);
    fputs(",\"dst_w\":", fp); json_positive_i32(fp, record->dst_w);
    fputs(",\"dst_h\":", fp); json_positive_i32(fp, record->dst_h);
    fputs(",\"dst_stride\":", fp); json_positive_i32(fp, record->dst_stride);
    fprintf(fp, ",\"rect_area\":"); json_u64(fp, record, LORIE_B3A_VALID_RECT, record->rect_area);
    fprintf(fp, ",\"src_area\":");
    if (record->src_area) fprintf(fp, "%llu", (unsigned long long) record->src_area); else fputs("null", fp);
    fprintf(fp, ",\"src_rect_ratio\":");
    if (record->src_area && record->rect_area) fprintf(fp, "%.6f", record->src_rect_ratio); else fputs("null", fp);
    fprintf(fp, ",\"requested_bytes\":");
    if (record->rect_area) fprintf(fp, "%llu", (unsigned long long) rect_bytes); else fputs("null", fp);
    fprintf(fp, ",\"batch_rects\":"); json_u64(fp, record, LORIE_B3A_VALID_BATCH, record->batch_rects);
    fprintf(fp, ",\"prepare_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_PREPARE, record->prepare_ns);
    fprintf(fp, ",\"promotion_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_PROMOTION, record->promotion_ns);
    fprintf(fp, ",\"ahb_allocate_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_AHB_ALLOC, record->ahb_allocate_ns);
    fprintf(fp, ",\"ahb_lock_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_AHB_LOCK, record->ahb_lock_ns);
    fprintf(fp, ",\"buffer_copy_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_BUFFER_COPY, record->buffer_copy_ns);
    fprintf(fp, ",\"ahb_unlock_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_AHB_UNLOCK, record->ahb_unlock_ns);
    fprintf(fp, ",\"fd_allocate_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_FD_ALLOC, record->fd_allocate_ns);
    fprintf(fp, ",\"fd_mmap_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_FD_MMAP, record->fd_mmap_ns);
    fprintf(fp, ",\"clone_bytes\":"); json_u64(fp, record, LORIE_B3A_VALID_CLONE, record->clone_bytes);
    fprintf(fp, ",\"clone_logical_bytes\":"); json_u64(fp, record, LORIE_B3A_VALID_CLONE, record->clone_logical_bytes);
    fprintf(fp, ",\"clone_physical_bytes\":"); json_u64(fp, record, LORIE_B3A_VALID_CLONE, record->clone_physical_bytes);
    fprintf(fp, ",\"clone_amplification\":");
    json_ratio(fp, record, LORIE_B3A_VALID_CLONE, record->clone_logical_bytes, rect_bytes);
    fprintf(fp, ",\"clone_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_CLONE, record->clone_ns);
    fprintf(fp, ",\"clone_copy_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_CLONE_COPY, record->clone_copy_ns);
    fprintf(fp, ",\"upload_bytes\":"); json_u64(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_bytes);
    fprintf(fp, ",\"upload_logical_bytes\":"); json_u64(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_logical_bytes);
    fprintf(fp, ",\"upload_physical_bytes\":"); json_u64(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_physical_bytes);
    fprintf(fp, ",\"upload_amplification\":");
    json_ratio(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_physical_bytes, rect_bytes);
    fprintf(fp, ",\"upload_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_ns);
    fprintf(fp, ",\"queue_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_QUEUE, record->queue_ns);
    fprintf(fp, ",\"draw_submit_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_DRAW_SUBMIT, record->draw_submit_ns);
    fputs(",\"gpu_exec_ns\":null", fp);
    fprintf(fp, ",\"fence_wait_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_FENCE, record->fence_wait_ns);
    fprintf(fp, ",\"done_composite_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_DONE, record->done_composite_ns);
    fprintf(fp, ",\"repair_bytes\":"); json_u64(fp, record, LORIE_B3A_VALID_REPAIR, record->repair_bytes);
    fprintf(fp, ",\"repair_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_REPAIR, record->repair_ns);
    fprintf(fp, ",\"wall_ns\":"); json_u64(fp, record, LORIE_B3A_VALID_WALL, record->wall_ns);
    fprintf(fp, ",\"fallback\":"); json_u64(fp, record, LORIE_B3A_VALID_FALLBACK, record->fallback);
    fprintf(fp, ",\"exact_fail\":"); json_u64(fp, record, LORIE_B3A_VALID_CORRECTNESS, record->exact_fail);
    fprintf(fp, ",\"max_delta\":"); json_u64(fp, record, LORIE_B3A_VALID_CORRECTNESS, record->max_delta);
    fprintf(fp, ",\"x_nz\":"); json_u64(fp, record, LORIE_B3A_VALID_CORRECTNESS, record->x_nz);
    fprintf(fp, ",\"staging_cache_hit\":"); json_u64(fp, record, LORIE_B3A_VALID_STAGING, record->staging_cache_hit);
    fprintf(fp, ",\"staging_cache_miss\":"); json_u64(fp, record, LORIE_B3A_VALID_STAGING, record->staging_cache_miss);
    fprintf(fp, ",\"staging_buffer_id\":"); json_u64(fp, record, LORIE_B3A_VALID_STAGING, record->staging_buffer_id);
    fputc('}', fp);
}

static void csv_u64(FILE *fp, const LorieB3aRecord *record, uint64_t bit, uint64_t value) {
    if (record->valid_mask & bit) fprintf(fp, "%llu", (unsigned long long) value); else fputs("NA", fp);
}

static void csv_record(FILE *fp, const LorieB3aRecord *record) {
    fprintf(fp, "%s,", b3a_candidate_name(record->candidate));
    csv_u64(fp, record, LORIE_B3A_VALID_SERIAL, record->serial); fputc(',', fp);
    csv_positive_i32(fp, record->rect_w); fputc(',', fp);
    csv_positive_i32(fp, record->rect_h); fputc(',', fp);
    csv_positive_i32(fp, record->src_w); fputc(',', fp);
    csv_positive_i32(fp, record->src_h); fputc(',', fp);
    csv_positive_i32(fp, record->src_stride); fputc(',', fp);
    csv_positive_i32(fp, record->dst_w); fputc(',', fp);
    csv_positive_i32(fp, record->dst_h); fputc(',', fp);
    csv_positive_i32(fp, record->dst_stride); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_RECT, record->rect_area); fputc(',', fp);
    if (record->src_area)
        fprintf(fp, "%llu", (unsigned long long) record->src_area);
    else
        fputs("NA", fp);
    fputc(',', fp);
    if (record->src_area && record->rect_area) fprintf(fp, "%.6f", record->src_rect_ratio); else fputs("NA", fp);
    fputc(',', fp); csv_u64(fp, record, LORIE_B3A_VALID_BATCH, record->batch_rects); fputc(',', fp);
    if (record->rect_area)
        fprintf(fp, "%llu", (unsigned long long) record->rect_area * 4ull);
    else
        fputs("NA", fp);
    fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_CLONE, record->clone_bytes); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_CLONE, record->clone_logical_bytes); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_CLONE, record->clone_physical_bytes); fputc(',', fp);
    csv_ratio(fp, record, LORIE_B3A_VALID_CLONE, record->clone_logical_bytes,
              record->rect_area * 4ull); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_PREPARE, record->prepare_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_PROMOTION, record->promotion_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_AHB_ALLOC, record->ahb_allocate_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_AHB_LOCK, record->ahb_lock_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_BUFFER_COPY, record->buffer_copy_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_AHB_UNLOCK, record->ahb_unlock_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_FD_ALLOC, record->fd_allocate_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_FD_MMAP, record->fd_mmap_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_CLONE, record->clone_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_CLONE_COPY, record->clone_copy_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_bytes); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_logical_bytes); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_physical_bytes); fputc(',', fp);
    csv_ratio(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_physical_bytes,
              record->rect_area * 4ull); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_UPLOAD, record->upload_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_QUEUE, record->queue_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_DRAW_SUBMIT, record->draw_submit_ns); fputs(",NA,", fp);
    csv_u64(fp, record, LORIE_B3A_VALID_FENCE, record->fence_wait_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_DONE, record->done_composite_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_REPAIR, record->repair_bytes); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_REPAIR, record->repair_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_WALL, record->wall_ns); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_FALLBACK, record->fallback); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_CORRECTNESS, record->exact_fail); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_CORRECTNESS, record->max_delta); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_CORRECTNESS, record->x_nz); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_STAGING, record->staging_cache_hit); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_STAGING, record->staging_cache_miss); fputc(',', fp);
    csv_u64(fp, record, LORIE_B3A_VALID_STAGING, record->staging_buffer_id); fputc('\n', fp);
}

void lorieB3aDump(LorieB3aTelemetry *telemetry, const char *reason) {
    const char *base;
    char json_path[1024], csv_path[1024];
    FILE *json, *csv;
    uint64_t count, i;
    if (!lorieB3aEnabled() || !telemetry)
        return;
    base = getenv("TERMUX_X11_B3A_OUTPUT");
    if (!base || !base[0])
        return;
    /* A CloseScreen/ddxGiveUp can fire before any Composite ran (screen
     * lifecycle, not benchmark end). Do not let an empty dump consume the
     * one-shot flag: dump-once means the first call WITH data, not the
     * first call. */
    if (telemetry->next_record == 0)
        return;
    if (__sync_lock_test_and_set(&telemetry->dumped, 1))
        return;
    telemetry->schema_version = LORIE_B3A_SCHEMA_VERSION;
    lorieB3aCaptureProcessEnd(telemetry, false);
    snprintf(json_path, sizeof(json_path), "%s.json", base);
    snprintf(csv_path, sizeof(csv_path), "%s.csv", base);
    json = fopen(json_path, "w");
    csv = fopen(csv_path, "w");
    if (!json || !csv) {
        if (json) fclose(json);
        if (csv) fclose(csv);
        return;
    }
    count = telemetry->next_record;
    if (count > LORIE_B3A_MAX_RECORDS)
        count = LORIE_B3A_MAX_RECORDS;
    fprintf(json, "{\"schema_version\":%u,\"reason\":\"%s\",\"next_record\":%llu,\"dropped_records\":%llu,\"metadata\":{\"x_rss_start_bytes\":%llu,\"x_rss_end_bytes\":%llu,\"x_fd_start\":%llu,\"x_fd_end\":%llu,\"renderer_rss_start_bytes\":%llu,\"renderer_rss_end_bytes\":%llu,\"renderer_fd_start\":%llu,\"renderer_fd_end\":%llu},\"records\":[",
            LORIE_B3A_SCHEMA_VERSION, reason ? reason : "unknown",
            (unsigned long long) telemetry->next_record, (unsigned long long) telemetry->dropped_records,
            (unsigned long long) telemetry->x_rss_start_bytes, (unsigned long long) telemetry->x_rss_end_bytes,
            (unsigned long long) telemetry->x_fd_start, (unsigned long long) telemetry->x_fd_end,
            (unsigned long long) telemetry->renderer_rss_start_bytes, (unsigned long long) telemetry->renderer_rss_end_bytes,
            (unsigned long long) telemetry->renderer_fd_start, (unsigned long long) telemetry->renderer_fd_end);
    for (i = 0; i < count; i++) {
        if (i) fputc(',', json);
        json_record(json, &telemetry->records[i]);
    }
    fputs("]}\n", json);
    fputs("candidate,serial,rect_w,rect_h,src_w,src_h,src_stride,dst_w,dst_h,dst_stride,rect_area,src_area,src_rect_ratio,batch_rects,requested_bytes,clone_bytes,clone_logical_bytes,clone_physical_bytes,clone_amplification,prepare_ns,promotion_ns,ahb_allocate_ns,ahb_lock_ns,buffer_copy_ns,ahb_unlock_ns,fd_allocate_ns,fd_mmap_ns,clone_ns,clone_copy_ns,upload_bytes,upload_logical_bytes,upload_physical_bytes,upload_amplification,upload_ns,queue_ns,draw_submit_ns,gpu_exec_ns,fence_wait_ns,done_composite_ns,repair_bytes,repair_ns,wall_ns,fallback,exact_fail,max_delta,x_nz,staging_cache_hit,staging_cache_miss,staging_buffer_id\n", csv);
    for (i = 0; i < count; i++)
        csv_record(csv, &telemetry->records[i]);
    fclose(json);
    fclose(csv);
}
