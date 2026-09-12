#define __ANDROID_UNAVAILABLE_SYMBOLS_ARE_WEAK__

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <android/hardware_buffer.h>
#include <android/log.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#include "buffer.h"
#include "egl_dispatch.h"
#include "gatea_a1_microprobe.h"

#define GATEA_A1_LOG(...) __android_log_print(ANDROID_LOG_INFO, "gatea-a1", __VA_ARGS__)

static const uint32_t kGateaA1Width = 2;
static const uint32_t kGateaA1Height = 2;
static const uint32_t kGateaA1Layers = 1;
static const uint32_t kGateaA1PhysicalRowBytes = 8;
static const uint64_t kGateaA1Usage =
        AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN |
        AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN |
        AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
        AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER;
static const uint32_t kGateaA1BgraFormat = AHARDWAREBUFFER_FORMAT_B8G8R8A8_UNORM;
static_assert(kGateaA1BgraFormat == 5, "Gate A1 BGRA format must remain the project value 5");

struct GateaA1Pixel {
    uint8_t r;
    uint8_t g;
    uint8_t b;
    uint8_t a;
};

/* Every logical byte is nonzero and distinct. The asymmetric matrix makes
 * horizontal, vertical, and 180-degree permutations observable. */
static const GateaA1Pixel kGateaA1LogicalPixels[4] = {
        { 0x13, 0x27, 0x3B, 0x4F },
        { 0x59, 0x6D, 0x71, 0x83 },
        { 0x97, 0xA9, 0xBD, 0xC1 },
        { 0xD7, 0xE3, 0xF1, 0xFB },
};

/* RGBX has no logical alpha byte. These X bytes are also distinctive,
 * nonzero, and intentionally not 0xFF so alpha forcing is observable. */
static const uint8_t kGateaA1RgbxX[4] = { 0x0B, 0x1D, 0x2F, 0x41 };

static const GLfloat kGateaA1QuadVertices[] = {
        -1.0f, -1.0f, 0.0f, 0.0f,
         1.0f, -1.0f, 1.0f, 0.0f,
        -1.0f,  1.0f, 0.0f, 1.0f,
         1.0f,  1.0f, 1.0f, 1.0f,
};

static const char kGateaA1VertexShader[] =
        "attribute vec4 a_position;\n"
        "attribute vec2 a_texcoord;\n"
        "varying vec2 v_texcoord;\n"
        "void main(void) {\n"
        "    gl_Position = a_position;\n"
        "    v_texcoord = a_texcoord;\n"
        "}\n";

static const char kGateaA1FragmentShader[] =
        "precision mediump float;\n"
        "varying vec2 v_texcoord;\n"
        "uniform sampler2D u_texture;\n"
        "void main(void) {\n"
        "    gl_FragColor = texture2D(u_texture, v_texcoord);\n"
        "}\n";

enum GateaA1Classification {
    GATEA_A1_ALLOC_FAIL = 0,
    GATEA_A1_LOCK_FAIL,
    GATEA_A1_EGL_CLIENT_BUFFER_FAIL,
    GATEA_A1_EGLIMAGE_CREATE_FAIL,
    GATEA_A1_GL_BIND_FAIL,
    GATEA_A1_FBO_FAIL,
    GATEA_A1_SHADER_COMPILE_FAIL,
    GATEA_A1_SHADER_LINK_FAIL,
    GATEA_A1_SHADER_SAMPLE_EXACT,
    GATEA_A1_SHADER_SAMPLE_BLACK,
    GATEA_A1_SHADER_SAMPLE_CHANNEL_SWAP,
    GATEA_A1_SHADER_SAMPLE_ORIENTATION_ERROR,
    GATEA_A1_SHADER_SAMPLE_OTHER_MISMATCH,
    GATEA_A1_UNLOCK_FAIL,
    GATEA_A1_CLASSIFICATION_COUNT,
};

static const GateaA1Classification kGateaA1Unclassified =
        (GateaA1Classification) -1;

struct GateaA1FormatSpec {
    const char *label;
    uint32_t value;
};

static const GateaA1FormatSpec kGateaA1Formats[3] = {
        { "CONTROL", AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM },
        { "BGRA_CANDIDATE", kGateaA1BgraFormat },
        { "RGBA_DIAGNOSTIC", AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM },
};

static const char *gateaA1ClassName(GateaA1Classification classification) {
    switch (classification) {
        case GATEA_A1_ALLOC_FAIL: return "ALLOC_FAIL";
        case GATEA_A1_LOCK_FAIL: return "LOCK_FAIL";
        case GATEA_A1_EGL_CLIENT_BUFFER_FAIL: return "EGL_CLIENT_BUFFER_FAIL";
        case GATEA_A1_EGLIMAGE_CREATE_FAIL: return "EGLIMAGE_CREATE_FAIL";
        case GATEA_A1_GL_BIND_FAIL: return "GL_BIND_FAIL";
        case GATEA_A1_FBO_FAIL: return "FBO_FAIL";
        case GATEA_A1_SHADER_COMPILE_FAIL: return "SHADER_COMPILE_FAIL";
        case GATEA_A1_SHADER_LINK_FAIL: return "SHADER_LINK_FAIL";
        case GATEA_A1_SHADER_SAMPLE_EXACT: return "SHADER_SAMPLE_EXACT";
        case GATEA_A1_SHADER_SAMPLE_BLACK: return "SHADER_SAMPLE_BLACK";
        case GATEA_A1_SHADER_SAMPLE_CHANNEL_SWAP: return "SHADER_SAMPLE_CHANNEL_SWAP";
        case GATEA_A1_SHADER_SAMPLE_ORIENTATION_ERROR: return "SHADER_SAMPLE_ORIENTATION_ERROR";
        case GATEA_A1_SHADER_SAMPLE_OTHER_MISMATCH: return "SHADER_SAMPLE_OTHER_MISMATCH";
        case GATEA_A1_UNLOCK_FAIL: return "UNLOCK_FAIL";
        default: return "UNCLASSIFIED";
    }
}

struct GateaA1RunRecord {
    const GateaA1FormatSpec *format;
    int run;
    AHardwareBuffer_Desc requested;
    AHardwareBuffer_Desc actual;
    bool actualDescriptorPresent;
    int allocStatus;
    int lockStatus;
    int unlockStatus;
    bool unlockAttempted;
    uint8_t physicalCpuRows[2][kGateaA1PhysicalRowBytes];
    GateaA1Pixel expected[4];

    bool clientBufferAttempted;
    EGLClientBuffer clientBuffer;
    EGLint clientBufferEglError;
    bool imageAttempted;
    bool imageCreated;
    EGLImageKHR image;
    EGLint imageCreateEglError;
    bool imageDestroyAttempted;
    EGLint imageDestroyEglError;

    GLuint sourceTexture;
    GLuint destinationTexture;
    GLuint destinationFbo;
    bool glObjectsTouched;
    bool glBindAttempted;
    GLenum glBindError;

    GLuint vertexShader;
    GLuint fragmentShader;
    GLuint program;
    GLint vertexCompileStatus;
    GLint fragmentCompileStatus;
    GLint shaderCompileStatus;
    GLenum vertexCompileGlError;
    GLenum fragmentCompileGlError;
    GLint linkStatus;
    GLenum linkGlError;
    GLint positionLocation;
    GLint texcoordLocation;
    GLint samplerLocation;
    char vertexCompileLog[256];
    char fragmentCompileLog[256];
    char linkLog[256];

    bool fboAttempted;
    GLenum fboStatus;
    GLenum fboGlError;
    bool drawAttempted;
    GLenum drawGlError;
    bool readbackAttempted;
    GLenum readbackGlError;
    uint8_t actualRgba[16];
    bool comparisonValid;
    uint32_t exactFailPixels;
    uint32_t maxChannelDelta;

    GateaA1Classification classification;
    const char *details;
};

static void gateaA1SetText(char *out, size_t capacity, const char *text) {
    size_t length;
    if (!out || capacity == 0)
        return;
    if (!text)
        text = "not_available";
    length = strlen(text);
    if (length >= capacity)
        length = capacity - 1;
    memcpy(out, text, length);
    out[length] = '\0';
}

static void gateaA1SanitizeLog(const char *raw, size_t length, char *out, size_t capacity) {
    size_t i;
    size_t offset = 0;
    if (!out || capacity == 0)
        return;
    if (!raw || length == 0) {
        gateaA1SetText(out, capacity, "empty");
        return;
    }
    for (i = 0; i < length && offset + 1 < capacity; i++) {
        unsigned char c = (unsigned char) raw[i];
        if (c < 0x20 || c > 0x7e || c == '=' || c == '\\')
            c = '_';
        out[offset++] = (char) c;
    }
    out[offset] = '\0';
    if (offset == 0)
        gateaA1SetText(out, capacity, "empty");
}

static void gateaA1FormatBytes(const uint8_t *bytes, size_t count, char *out, size_t capacity) {
    size_t i;
    size_t offset = 0;
    if (!out || capacity == 0)
        return;
    out[0] = '\0';
    for (i = 0; i < count && offset + 1 < capacity; i++) {
        int written = snprintf(out + offset, capacity - offset, "%s%02X",
                               i == 0 ? "" : ",", (unsigned int) bytes[i]);
        if (written < 0)
            break;
        if ((size_t) written >= capacity - offset) {
            offset = capacity - 1;
            break;
        }
        offset += (size_t) written;
    }
    out[offset] = '\0';
}

static void gateaA1FormatPixels(const GateaA1Pixel pixels[4], char *out, size_t capacity) {
    size_t i;
    size_t offset = 0;
    if (!out || capacity == 0)
        return;
    out[0] = '\0';
    for (i = 0; i < 4 && offset + 1 < capacity; i++) {
        int written = snprintf(out + offset, capacity - offset,
                               "%s%02X%02X%02X%02X", i == 0 ? "" : ",",
                               (unsigned int) pixels[i].r,
                               (unsigned int) pixels[i].g,
                               (unsigned int) pixels[i].b,
                               (unsigned int) pixels[i].a);
        if (written < 0)
            break;
        if ((size_t) written >= capacity - offset) {
            offset = capacity - 1;
            break;
        }
        offset += (size_t) written;
    }
    out[offset] = '\0';
}

static GLenum gateaA1ConsumeGlErrors(void) {
    GLenum first = GL_NO_ERROR;
    GLenum error;
    while ((error = glGetError()) != GL_NO_ERROR) {
        if (first == GL_NO_ERROR)
            first = error;
    }
    return first;
}

static void gateaA1ClearEglErrors(void) {
    while (eglGetError() != EGL_SUCCESS)
        ;
}

static void gateaA1RecordFirstGlError(GLenum *slot, GLenum error) {
    if (slot && *slot == GL_NO_ERROR && error != GL_NO_ERROR)
        *slot = error;
}

static GLenum gateaA1FirstGlError(GLenum first, GLenum second) {
    return first != GL_NO_ERROR ? first : second;
}

static void gateaA1ExpectedPixels(const GateaA1FormatSpec *format, GateaA1Pixel expected[4]) {
    size_t i;
    for (i = 0; i < 4; i++) {
        expected[i] = kGateaA1LogicalPixels[i];
        if (format->value == AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM)
            expected[i].a = 0xFF;
    }
}

static void gateaA1PhysicalPixel(const GateaA1FormatSpec *format, size_t index, uint8_t out[4]) {
    const GateaA1Pixel *pixel = &kGateaA1LogicalPixels[index];
    if (format->value == kGateaA1BgraFormat) {
        out[0] = pixel->b;
        out[1] = pixel->g;
        out[2] = pixel->r;
        out[3] = pixel->a;
    } else if (format->value == AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM) {
        out[0] = pixel->r;
        out[1] = pixel->g;
        out[2] = pixel->b;
        out[3] = kGateaA1RgbxX[index];
    } else {
        out[0] = pixel->r;
        out[1] = pixel->g;
        out[2] = pixel->b;
        out[3] = pixel->a;
    }
}

static void gateaA1WritePixels(const GateaA1FormatSpec *format,
                               const AHardwareBuffer_Desc *actual, void *mapped,
                               uint8_t physicalRows[2][kGateaA1PhysicalRowBytes]) {
    uint32_t y;
    uint32_t x;
    for (y = 0; y < kGateaA1Height; y++) {
        uint8_t *row = (uint8_t *) mapped +
                (size_t) y * (size_t) actual->stride * sizeof(GateaA1Pixel);
        for (x = 0; x < kGateaA1Width; x++) {
            uint8_t physical[4];
            gateaA1PhysicalPixel(format, (size_t) y * kGateaA1Width + x, physical);
            memcpy(row + (size_t) x * sizeof(GateaA1Pixel), physical, sizeof(physical));
        }
        memcpy(physicalRows[y], row, kGateaA1PhysicalRowBytes);
    }
}

static bool gateaA1DescriptorMatches(const AHardwareBuffer_Desc *actual,
                                     const AHardwareBuffer_Desc *requested) {
    if (!actual || !requested || actual->width != kGateaA1Width ||
        actual->height != kGateaA1Height || actual->layers != kGateaA1Layers ||
        actual->stride < kGateaA1Width || actual->format != requested->format ||
        (actual->usage & kGateaA1Usage) != kGateaA1Usage)
        return false;
    if ((size_t) actual->stride > SIZE_MAX /
            (sizeof(GateaA1Pixel) * kGateaA1Height))
        return false;
    return true;
}

static void gateaA1Fail(GateaA1RunRecord *record, GateaA1Classification classification,
                        const char *details) {
    if (record->classification == kGateaA1Unclassified) {
        record->classification = classification;
        record->details = details;
    }
}

static GLenum gateaA1CollectShaderLog(GLuint shader, char *out, size_t capacity) {
    GLint infoLength = 0;
    GLsizei returned = 0;
    char raw[512];
    GLenum error;

    glGetShaderiv(shader, GL_INFO_LOG_LENGTH, &infoLength);
    error = gateaA1ConsumeGlErrors();
    if (error != GL_NO_ERROR) {
        gateaA1SetText(out, capacity, "info_log_query_error");
        return error;
    }
    if (infoLength <= 0) {
        gateaA1SetText(out, capacity, "empty");
        return GL_NO_ERROR;
    }
    memset(raw, 0, sizeof(raw));
    glGetShaderInfoLog(shader, (GLsizei) sizeof(raw), &returned, raw);
    error = gateaA1ConsumeGlErrors();
    if (error != GL_NO_ERROR) {
        gateaA1SetText(out, capacity, "info_log_read_error");
        return error;
    }
    if (returned < 0)
        returned = 0;
    if ((size_t) returned > sizeof(raw))
        returned = (GLsizei) sizeof(raw);
    gateaA1SanitizeLog(raw, (size_t) returned, out, capacity);
    return GL_NO_ERROR;
}

static GLenum gateaA1CollectProgramLog(GLuint program, char *out, size_t capacity) {
    GLint infoLength = 0;
    GLsizei returned = 0;
    char raw[512];
    GLenum error;

    glGetProgramiv(program, GL_INFO_LOG_LENGTH, &infoLength);
    error = gateaA1ConsumeGlErrors();
    if (error != GL_NO_ERROR) {
        gateaA1SetText(out, capacity, "info_log_query_error");
        return error;
    }
    if (infoLength <= 0) {
        gateaA1SetText(out, capacity, "empty");
        return GL_NO_ERROR;
    }
    memset(raw, 0, sizeof(raw));
    glGetProgramInfoLog(program, (GLsizei) sizeof(raw), &returned, raw);
    error = gateaA1ConsumeGlErrors();
    if (error != GL_NO_ERROR) {
        gateaA1SetText(out, capacity, "info_log_read_error");
        return error;
    }
    if (returned < 0)
        returned = 0;
    if ((size_t) returned > sizeof(raw))
        returned = (GLsizei) sizeof(raw);
    gateaA1SanitizeLog(raw, (size_t) returned, out, capacity);
    return GL_NO_ERROR;
}

static GLuint gateaA1CompileShader(GLenum type, const char *source, GLint *status,
                                   GLenum *apiError, char *log, size_t logCapacity) {
    GLuint shader;
    GLenum error;
    GLenum logError;

    *status = -1;
    *apiError = GL_NO_ERROR;
    gateaA1SetText(log, logCapacity, "not_run");
    shader = glCreateShader(type);
    error = gateaA1ConsumeGlErrors();
    if (!shader) {
        *status = GL_FALSE;
        *apiError = error;
        gateaA1SetText(log, logCapacity, "glCreateShader_failed");
        return 0;
    }
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    error = gateaA1ConsumeGlErrors();
    glGetShaderiv(shader, GL_COMPILE_STATUS, status);
    error = gateaA1FirstGlError(error, gateaA1ConsumeGlErrors());
    logError = gateaA1CollectShaderLog(shader, log, logCapacity);
    error = gateaA1FirstGlError(error, logError);
    *apiError = error;
    return shader;
}

static GLuint gateaA1LinkProgram(GLuint vertexShader, GLuint fragmentShader, GLint *status,
                                 GLenum *apiError, char *log, size_t logCapacity) {
    GLuint program;
    GLenum error;
    GLenum logError;

    *status = -1;
    *apiError = GL_NO_ERROR;
    gateaA1SetText(log, logCapacity, "not_run");
    program = glCreateProgram();
    error = gateaA1ConsumeGlErrors();
    if (!program) {
        *status = GL_FALSE;
        *apiError = error;
        gateaA1SetText(log, logCapacity, "glCreateProgram_failed");
        return 0;
    }
    glAttachShader(program, vertexShader);
    glAttachShader(program, fragmentShader);
    glBindAttribLocation(program, 0, "a_position");
    glBindAttribLocation(program, 1, "a_texcoord");
    error = gateaA1ConsumeGlErrors();
    glLinkProgram(program);
    error = gateaA1FirstGlError(error, gateaA1ConsumeGlErrors());
    glGetProgramiv(program, GL_LINK_STATUS, status);
    error = gateaA1FirstGlError(error, gateaA1ConsumeGlErrors());
    logError = gateaA1CollectProgramLog(program, log, logCapacity);
    error = gateaA1FirstGlError(error, logError);
    *apiError = error;
    return program;
}

struct GateaA1GlState {
    GLint framebuffer;
    GLint viewport[4];
    GLint currentProgram;
    GLint activeTexture;
    GLint activeTextureBinding;
    GLint texture0Binding;
    GLint arrayBuffer;
    GLint packAlignment;
    GLint vertexAttrib0Enabled;
    GLint vertexAttrib1Enabled;
    GLboolean blendEnabled;
};

static void gateaA1CaptureGlState(GateaA1GlState *state) {
    memset(state, 0, sizeof(*state));
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &state->framebuffer);
    glGetIntegerv(GL_VIEWPORT, state->viewport);
    glGetIntegerv(GL_CURRENT_PROGRAM, &state->currentProgram);
    glGetIntegerv(GL_ACTIVE_TEXTURE, &state->activeTexture);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &state->activeTextureBinding);
    state->texture0Binding = state->activeTextureBinding;
    if (state->activeTexture != (GLint) GL_TEXTURE0) {
        glActiveTexture(GL_TEXTURE0);
        gateaA1ConsumeGlErrors();
        glGetIntegerv(GL_TEXTURE_BINDING_2D, &state->texture0Binding);
        glActiveTexture((GLenum) state->activeTexture);
        gateaA1ConsumeGlErrors();
    }
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &state->arrayBuffer);
    glGetIntegerv(GL_PACK_ALIGNMENT, &state->packAlignment);
    glGetVertexAttribiv(0, GL_VERTEX_ATTRIB_ARRAY_ENABLED, &state->vertexAttrib0Enabled);
    glGetVertexAttribiv(1, GL_VERTEX_ATTRIB_ARRAY_ENABLED, &state->vertexAttrib1Enabled);
    state->blendEnabled = glIsEnabled(GL_BLEND);
}

static GLenum gateaA1RestoreGlState(const GateaA1GlState *state) {
    glBindFramebuffer(GL_FRAMEBUFFER, (GLuint) state->framebuffer);
    glViewport(state->viewport[0], state->viewport[1],
               state->viewport[2], state->viewport[3]);
    glUseProgram((GLuint) state->currentProgram);
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint) state->arrayBuffer);
    glPixelStorei(GL_PACK_ALIGNMENT, state->packAlignment);

    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, (GLuint) state->texture0Binding);
    if (state->activeTexture != (GLint) GL_TEXTURE0) {
        glActiveTexture((GLenum) state->activeTexture);
        glBindTexture(GL_TEXTURE_2D, (GLuint) state->activeTextureBinding);
    }
    if (state->vertexAttrib0Enabled)
        glEnableVertexAttribArray(0);
    else
        glDisableVertexAttribArray(0);
    if (state->vertexAttrib1Enabled)
        glEnableVertexAttribArray(1);
    else
        glDisableVertexAttribArray(1);
    if (state->blendEnabled)
        glEnable(GL_BLEND);
    else
        glDisable(GL_BLEND);
    return gateaA1ConsumeGlErrors();
}

static void gateaA1ActualPixels(const GateaA1RunRecord *record, GateaA1Pixel actual[4]) {
    size_t i;
    for (i = 0; i < 4; i++) {
        actual[i].r = record->actualRgba[i * 4 + 0];
        actual[i].g = record->actualRgba[i * 4 + 1];
        actual[i].b = record->actualRgba[i * 4 + 2];
        actual[i].a = record->actualRgba[i * 4 + 3];
    }
}

static bool gateaA1PixelsEqual(const GateaA1Pixel *left, const GateaA1Pixel *right) {
    return left->r == right->r && left->g == right->g &&
           left->b == right->b && left->a == right->a;
}

static bool gateaA1PermutationMatches(const GateaA1Pixel actual[4],
                                      const GateaA1Pixel expected[4],
                                      const size_t permutation[4]) {
    size_t i;
    for (i = 0; i < 4; i++)
        if (!gateaA1PixelsEqual(&actual[i], &expected[permutation[i]]))
            return false;
    return true;
}

static bool gateaA1ChannelSwapMatches(const GateaA1Pixel actual[4],
                                      const GateaA1Pixel expected[4]) {
    size_t i;
    for (i = 0; i < 4; i++) {
        if (actual[i].r != expected[i].b || actual[i].g != expected[i].g ||
            actual[i].b != expected[i].r || actual[i].a != expected[i].a)
            return false;
    }
    return true;
}

static void gateaA1ClassifyPixels(GateaA1RunRecord *record) {
    static const size_t identity[4] = { 0, 1, 2, 3 };
    static const size_t horizontal[4] = { 1, 0, 3, 2 };
    static const size_t vertical[4] = { 2, 3, 0, 1 };
    static const size_t rotate180[4] = { 3, 2, 1, 0 };
    GateaA1Pixel actual[4];
    size_t i;
    bool allBlack = true;

    gateaA1ActualPixels(record, actual);
    for (i = 0; i < 4; i++) {
        if (actual[i].r != 0 || actual[i].g != 0 || actual[i].b != 0) {
            allBlack = false;
            break;
        }
    }
    if (gateaA1PermutationMatches(actual, record->expected, identity)) {
        record->classification = GATEA_A1_SHADER_SAMPLE_EXACT;
        record->details = "exact";
    } else if (allBlack) {
        record->classification = GATEA_A1_SHADER_SAMPLE_BLACK;
        record->details = "all_zero_rgb";
    } else if (gateaA1PermutationMatches(actual, record->expected, horizontal)) {
        record->classification = GATEA_A1_SHADER_SAMPLE_ORIENTATION_ERROR;
        record->details = "horizontal_flip";
    } else if (gateaA1PermutationMatches(actual, record->expected, vertical)) {
        record->classification = GATEA_A1_SHADER_SAMPLE_ORIENTATION_ERROR;
        record->details = "vertical_flip";
    } else if (gateaA1PermutationMatches(actual, record->expected, rotate180)) {
        record->classification = GATEA_A1_SHADER_SAMPLE_ORIENTATION_ERROR;
        record->details = "rotate_180";
    } else if (gateaA1ChannelSwapMatches(actual, record->expected)) {
        record->classification = GATEA_A1_SHADER_SAMPLE_CHANNEL_SWAP;
        record->details = "r_b_swap";
    } else {
        record->classification = GATEA_A1_SHADER_SAMPLE_OTHER_MISMATCH;
        record->details = "pixel_mismatch";
    }
}

static void gateaA1ComparePixels(GateaA1RunRecord *record) {
    GateaA1Pixel actual[4];
    size_t i;
    size_t channel;
    uint32_t failures = 0;
    uint32_t maxDelta = 0;

    gateaA1ActualPixels(record, actual);
    for (i = 0; i < 4; i++) {
        const uint8_t expected[4] = {
                actual[i].r, actual[i].g, actual[i].b, actual[i].a
        };
        const uint8_t wanted[4] = {
                record->expected[i].r, record->expected[i].g,
                record->expected[i].b, record->expected[i].a
        };
        bool failed = false;
        for (channel = 0; channel < 4; channel++) {
            uint32_t delta = expected[channel] > wanted[channel]
                    ? (uint32_t) expected[channel] - wanted[channel]
                    : (uint32_t) wanted[channel] - expected[channel];
            if (delta > maxDelta)
                maxDelta = delta;
            if (delta != 0)
                failed = true;
        }
        if (failed)
            failures++;
    }
    record->exactFailPixels = failures;
    record->maxChannelDelta = maxDelta;
}

static void gateaA1InitializeRecord(GateaA1RunRecord *record,
                                    const GateaA1FormatSpec *format, int run) {
    memset(record, 0, sizeof(*record));
    record->format = format;
    record->run = run;
    record->requested.width = kGateaA1Width;
    record->requested.height = kGateaA1Height;
    record->requested.layers = kGateaA1Layers;
    record->requested.format = format->value;
    record->requested.usage = kGateaA1Usage;
    record->allocStatus = -1;
    record->lockStatus = -1;
    record->unlockStatus = -1;
    record->clientBufferEglError = EGL_SUCCESS;
    record->imageCreateEglError = EGL_SUCCESS;
    record->imageDestroyEglError = EGL_SUCCESS;
    record->image = EGL_NO_IMAGE_KHR;
    record->vertexCompileStatus = -1;
    record->fragmentCompileStatus = -1;
    record->shaderCompileStatus = -1;
    record->linkStatus = -1;
    record->positionLocation = -1;
    record->texcoordLocation = -1;
    record->samplerLocation = -1;
    record->classification = kGateaA1Unclassified;
    record->details = "not_classified";
    gateaA1ExpectedPixels(format, record->expected);
    gateaA1SetText(record->vertexCompileLog, sizeof(record->vertexCompileLog), "not_run");
    gateaA1SetText(record->fragmentCompileLog, sizeof(record->fragmentCompileLog), "not_run");
    gateaA1SetText(record->linkLog, sizeof(record->linkLog), "not_run");
}

static void gateaA1RunOne(EGLDisplay egl_display, const GateaA1FormatSpec *format,
                          int run, bool glReady, GateaA1RunRecord *record) {
    AHardwareBuffer *buffer = NULL;
    void *mapped = NULL;
    bool locked = false;
    GLenum error;

    gateaA1InitializeRecord(record, format, run);
    do {
        if (!glReady) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL,
                        "borrowed_context_or_surface_unavailable");
            break;
        }
        if (!__builtin_available(android 26, *)) {
            gateaA1Fail(record, GATEA_A1_ALLOC_FAIL, "android_api_below_26");
            break;
        }

        record->allocStatus = AHardwareBuffer_allocate(&record->requested, &buffer);
        if (record->allocStatus != 0 || !buffer) {
            gateaA1Fail(record, GATEA_A1_ALLOC_FAIL,
                        record->allocStatus != 0 ? "AHardwareBuffer_allocate_error"
                                                 : "AHardwareBuffer_allocate_null");
            break;
        }
        AHardwareBuffer_describe(buffer, &record->actual);
        record->actualDescriptorPresent = true;
        if (!gateaA1DescriptorMatches(&record->actual, &record->requested)) {
            gateaA1Fail(record, GATEA_A1_ALLOC_FAIL, "actual_descriptor_mismatch");
            break;
        }

        record->lockStatus = AHardwareBuffer_lock(
                buffer,
                AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN | AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN,
                -1, NULL, &mapped);
        if (record->lockStatus == 0)
            locked = true;
        if (record->lockStatus != 0) {
            gateaA1Fail(record, GATEA_A1_LOCK_FAIL, "AHardwareBuffer_lock_error");
            break;
        }
        if (!mapped || mapped == MAP_FAILED) {
            gateaA1Fail(record, GATEA_A1_LOCK_FAIL, "AHardwareBuffer_lock_null_mapping");
            break;
        }
        gateaA1WritePixels(format, &record->actual, mapped, record->physicalCpuRows);

        /* A successful blocking unlock is only the CPU-write completion
         * boundary for this microprobe; no acquire-fence meaning is inferred. */
        record->unlockAttempted = true;
        record->unlockStatus = AHardwareBuffer_unlock(buffer, NULL);
        locked = false;
        if (record->unlockStatus != 0) {
            gateaA1Fail(record, GATEA_A1_UNLOCK_FAIL,
                        "AHardwareBuffer_unlock_error_cpu_write_boundary_failed");
            break;
        }

        if (!lorieEglHasNativeClientBuffer() || !lorieEglGetNativeClientBufferANDROID) {
            gateaA1Fail(record, GATEA_A1_EGL_CLIENT_BUFFER_FAIL,
                        "native_client_buffer_extension_or_proc_unavailable");
            break;
        }
        gateaA1ClearEglErrors();
        record->clientBufferAttempted = true;
        record->clientBuffer = lorieEglGetNativeClientBufferANDROID(buffer);
        record->clientBufferEglError = eglGetError();
        if (!record->clientBuffer || record->clientBufferEglError != EGL_SUCCESS) {
            gateaA1Fail(record, GATEA_A1_EGL_CLIENT_BUFFER_FAIL,
                        record->clientBuffer ? "eglGetNativeClientBufferANDROID_error"
                                             : "eglGetNativeClientBufferANDROID_null");
            break;
        }

        if (!lorieEglHasImage() || !lorieEglCreateImageKHR || !lorieEglDestroyImageKHR) {
            gateaA1Fail(record, GATEA_A1_EGLIMAGE_CREATE_FAIL,
                        "egl_image_extension_or_proc_unavailable");
            break;
        }
        {
            const EGLint imageAttributes[] = {
                    EGL_IMAGE_PRESERVED_KHR, EGL_TRUE, EGL_NONE
            };
            gateaA1ClearEglErrors();
            record->imageAttempted = true;
            record->image = lorieEglCreateImageKHR(
                    egl_display, EGL_NO_CONTEXT, EGL_NATIVE_BUFFER_ANDROID,
                    record->clientBuffer, imageAttributes);
            record->imageCreateEglError = eglGetError();
            record->imageCreated = record->image != EGL_NO_IMAGE_KHR;
        }
        if (!record->imageCreated || record->imageCreateEglError != EGL_SUCCESS) {
            gateaA1Fail(record, GATEA_A1_EGLIMAGE_CREATE_FAIL,
                        record->imageCreated ? "eglCreateImageKHR_error"
                                             : "eglCreateImageKHR_null");
            break;
        }

        record->glObjectsTouched = true;
        glActiveTexture(GL_TEXTURE0);
        glGenTextures(1, &record->sourceTexture);
        error = gateaA1ConsumeGlErrors();
        gateaA1RecordFirstGlError(&record->glBindError, error);
        glGenTextures(1, &record->destinationTexture);
        error = gateaA1ConsumeGlErrors();
        gateaA1RecordFirstGlError(&record->glBindError, error);
        record->glBindAttempted = true;
        if (!record->sourceTexture || !record->destinationTexture) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "texture_generation_failed");
            break;
        }
        if (record->sourceTexture == record->destinationTexture) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL,
                        "source_destination_texture_ids_not_distinct");
            break;
        }
        if (record->glBindError != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "texture_generation_gl_error");
            break;
        }

        glBindTexture(GL_TEXTURE_2D, record->sourceTexture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        error = gateaA1ConsumeGlErrors();
        gateaA1RecordFirstGlError(&record->glBindError, error);
        if (error != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "source_texture_bind_gl_error");
            break;
        }
        if (!lorieGlesHasEglImage() || !lorieGlEGLImageTargetTexture2DOES) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL,
                        "GL_OES_EGL_image_extension_or_proc_unavailable");
            break;
        }
        lorieGlEGLImageTargetTexture2DOES(GL_TEXTURE_2D, record->image);
        error = gateaA1ConsumeGlErrors();
        gateaA1RecordFirstGlError(&record->glBindError, error);
        if (error != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL,
                        "glEGLImageTargetTexture2DOES_gl_error");
            break;
        }

        record->vertexShader = gateaA1CompileShader(
                GL_VERTEX_SHADER, kGateaA1VertexShader,
                &record->vertexCompileStatus, &record->vertexCompileGlError,
                record->vertexCompileLog, sizeof(record->vertexCompileLog));
        record->fragmentShader = gateaA1CompileShader(
                GL_FRAGMENT_SHADER, kGateaA1FragmentShader,
                &record->fragmentCompileStatus, &record->fragmentCompileGlError,
                record->fragmentCompileLog, sizeof(record->fragmentCompileLog));
        record->shaderCompileStatus =
                record->vertexCompileStatus == GL_TRUE &&
                record->fragmentCompileStatus == GL_TRUE &&
                record->vertexCompileGlError == GL_NO_ERROR &&
                record->fragmentCompileGlError == GL_NO_ERROR ? GL_TRUE : GL_FALSE;
        if (record->shaderCompileStatus != GL_TRUE) {
            gateaA1Fail(record, GATEA_A1_SHADER_COMPILE_FAIL,
                        "shader_compile_status_or_gl_error");
            break;
        }

        record->program = gateaA1LinkProgram(
                record->vertexShader, record->fragmentShader,
                &record->linkStatus, &record->linkGlError,
                record->linkLog, sizeof(record->linkLog));
        if (!record->program || record->linkStatus != GL_TRUE ||
            record->linkGlError != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_SHADER_LINK_FAIL,
                        "shader_link_status_or_gl_error");
            break;
        }
        record->positionLocation = glGetAttribLocation(record->program, "a_position");
        record->texcoordLocation = glGetAttribLocation(record->program, "a_texcoord");
        record->samplerLocation = glGetUniformLocation(record->program, "u_texture");
        error = gateaA1ConsumeGlErrors();
        if (error != GL_NO_ERROR || record->positionLocation != 0 ||
            record->texcoordLocation != 1 || record->samplerLocation < 0) {
            record->linkGlError = gateaA1FirstGlError(record->linkGlError, error);
            gateaA1Fail(record, GATEA_A1_SHADER_LINK_FAIL,
                        error != GL_NO_ERROR ? "shader_interface_query_gl_error"
                                             : "shader_interface_location_invalid");
            break;
        }

        glBindTexture(GL_TEXTURE_2D, record->destinationTexture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, (GLsizei) kGateaA1Width,
                     (GLsizei) kGateaA1Height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
        error = gateaA1ConsumeGlErrors();
        gateaA1RecordFirstGlError(&record->glBindError, error);
        if (error != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL,
                        "destination_texture_setup_gl_error");
            break;
        }

        record->fboAttempted = true;
        glGenFramebuffers(1, &record->destinationFbo);
        record->fboGlError = gateaA1ConsumeGlErrors();
        if (record->fboGlError != GL_NO_ERROR || !record->destinationFbo) {
            gateaA1Fail(record, GATEA_A1_FBO_FAIL, "framebuffer_generation_gl_error");
            break;
        }
        glBindFramebuffer(GL_FRAMEBUFFER, record->destinationFbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, record->destinationTexture, 0);
        record->fboStatus = glCheckFramebufferStatus(GL_FRAMEBUFFER);
        error = gateaA1ConsumeGlErrors();
        record->fboGlError = gateaA1FirstGlError(record->fboGlError, error);
        if (record->fboGlError != GL_NO_ERROR ||
            record->fboStatus != GL_FRAMEBUFFER_COMPLETE) {
            gateaA1Fail(record, GATEA_A1_FBO_FAIL,
                        record->fboGlError != GL_NO_ERROR ? "framebuffer_setup_gl_error"
                                                          : "framebuffer_incomplete");
            break;
        }

        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, record->sourceTexture);
        error = gateaA1ConsumeGlErrors();
        gateaA1RecordFirstGlError(&record->glBindError, error);
        if (error != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL,
                        "source_texture_rebind_gl_error");
            break;
        }

        glViewport(0, 0, (GLsizei) kGateaA1Width, (GLsizei) kGateaA1Height);
        glDisable(GL_BLEND);
        glUseProgram(record->program);
        glUniform1i(record->samplerLocation, 0);
        glBindBuffer(GL_ARRAY_BUFFER, 0);
        glVertexAttribPointer((GLuint) record->positionLocation, 2, GL_FLOAT,
                              GL_FALSE, (GLsizei) (4 * sizeof(GLfloat)),
                              kGateaA1QuadVertices);
        glVertexAttribPointer((GLuint) record->texcoordLocation, 2, GL_FLOAT,
                              GL_FALSE, (GLsizei) (4 * sizeof(GLfloat)),
                              kGateaA1QuadVertices + 2);
        glEnableVertexAttribArray((GLuint) record->positionLocation);
        glEnableVertexAttribArray((GLuint) record->texcoordLocation);
        error = gateaA1ConsumeGlErrors();
        if (error != GL_NO_ERROR) {
            record->drawGlError = error;
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "draw_setup_gl_error");
            break;
        }

        record->drawAttempted = true;
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        record->drawGlError = gateaA1ConsumeGlErrors();
        if (record->drawGlError != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "draw_gl_error");
            break;
        }
        glFinish();
        record->drawGlError = gateaA1ConsumeGlErrors();
        if (record->drawGlError != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "draw_finish_gl_error");
            break;
        }

        glPixelStorei(GL_PACK_ALIGNMENT, 4);
        error = gateaA1ConsumeGlErrors();
        if (error != GL_NO_ERROR) {
            record->readbackGlError = error;
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "readback_setup_gl_error");
            break;
        }
        record->readbackAttempted = true;
        glReadPixels(0, 0, (GLsizei) kGateaA1Width, (GLsizei) kGateaA1Height,
                     GL_RGBA, GL_UNSIGNED_BYTE, record->actualRgba);
        record->readbackGlError = gateaA1ConsumeGlErrors();
        if (record->readbackGlError != GL_NO_ERROR) {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "readback_gl_error");
            break;
        }
        record->comparisonValid = true;
    } while (false);

    if (locked) {
        if (__builtin_available(android 26, *)) {
            record->unlockAttempted = true;
            record->unlockStatus = AHardwareBuffer_unlock(buffer, NULL);
            locked = false;
            if (record->unlockStatus != 0)
                gateaA1Fail(record, GATEA_A1_UNLOCK_FAIL,
                            "AHardwareBuffer_unlock_error_cleanup");
        }
    }

    if (record->glObjectsTouched) {
        glFinish();
        error = gateaA1ConsumeGlErrors();
        if (error != GL_NO_ERROR)
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "teardown_glFinish_error");
        if (record->program)
            glDeleteProgram(record->program);
        if (record->vertexShader)
            glDeleteShader(record->vertexShader);
        if (record->fragmentShader)
            glDeleteShader(record->fragmentShader);
        if (record->destinationFbo)
            glDeleteFramebuffers(1, &record->destinationFbo);
        if (record->destinationTexture)
            glDeleteTextures(1, &record->destinationTexture);
        if (record->sourceTexture)
            glDeleteTextures(1, &record->sourceTexture);
        gateaA1ConsumeGlErrors();
    }

    if (record->imageCreated) {
        if (lorieEglDestroyImageKHR) {
            gateaA1ClearEglErrors();
            record->imageDestroyAttempted = true;
            if (lorieEglDestroyImageKHR(egl_display, record->image) != EGL_TRUE)
                gateaA1Fail(record, GATEA_A1_EGLIMAGE_CREATE_FAIL,
                            "eglDestroyImageKHR_failed");
            record->imageDestroyEglError = eglGetError();
            if (record->imageDestroyEglError != EGL_SUCCESS)
                gateaA1Fail(record, GATEA_A1_EGLIMAGE_CREATE_FAIL,
                            "eglDestroyImageKHR_error");
        } else {
            gateaA1Fail(record, GATEA_A1_EGLIMAGE_CREATE_FAIL,
                        "eglDestroyImageKHR_unavailable_after_create");
        }
        record->image = EGL_NO_IMAGE_KHR;
    }

    if (buffer) {
        if (__builtin_available(android 26, *))
            AHardwareBuffer_release(buffer);
        buffer = NULL;
    }

    if (record->classification == kGateaA1Unclassified) {
        if (record->comparisonValid) {
            gateaA1ComparePixels(record);
            gateaA1ClassifyPixels(record);
        } else {
            gateaA1Fail(record, GATEA_A1_GL_BIND_FAIL, "sample_not_completed");
        }
    }
}

static void gateaA1LogRun(const GateaA1RunRecord *record) {
    char logical[64];
    char expected[64];
    char actual[64];
    char physical0[64];
    char physical1[64];
    gateaA1FormatPixels(kGateaA1LogicalPixels, logical, sizeof(logical));
    gateaA1FormatPixels(record->expected, expected, sizeof(expected));
    {
        GateaA1Pixel actualPixels[4];
        gateaA1ActualPixels(record, actualPixels);
        gateaA1FormatPixels(actualPixels, actual, sizeof(actual));
    }
    gateaA1FormatBytes(record->physicalCpuRows[0], kGateaA1PhysicalRowBytes,
                       physical0, sizeof(physical0));
    gateaA1FormatBytes(record->physicalCpuRows[1], kGateaA1PhysicalRowBytes,
                       physical1, sizeof(physical1));

    GATEA_A1_LOG(
            "GATEA_A1 run format=%s format_value=%u run=%d "
            "requested_width=%u requested_height=%u requested_layers=%u "
            "requested_stride=%u requested_format=%u requested_usage=0x%llx "
            "actual_desc_present=%d actual_width=%u actual_height=%u actual_layers=%u "
            "actual_stride=%u actual_format=%u actual_usage=0x%llx "
            "alloc_status=%d lock_status=%d unlock_attempted=%d unlock_status=%d "
            "physical_cpu_row_bytes=%u physical_cpu_row0=%s physical_cpu_row1=%s "
            "logical_expected_rgba=%s expected_rgba=%s",
            record->format->label, (unsigned int) record->format->value, record->run,
            record->requested.width, record->requested.height, record->requested.layers,
            record->requested.stride, record->requested.format,
            (unsigned long long) record->requested.usage,
            record->actualDescriptorPresent ? 1 : 0,
            record->actual.width, record->actual.height, record->actual.layers,
            record->actual.stride, record->actual.format,
            (unsigned long long) record->actual.usage,
            record->allocStatus, record->lockStatus,
            record->unlockAttempted ? 1 : 0, record->unlockStatus,
            (unsigned int) kGateaA1PhysicalRowBytes, physical0, physical1,
            logical, expected);

    GATEA_A1_LOG(
            "GATEA_A1 run format=%s format_value=%u run=%d "
            "client_buffer_attempted=%d client_buffer=%p client_buffer_result=%s "
            "client_buffer_egl_error=0x%04x image_attempted=%d image_result=%s "
            "image_create_egl_error=0x%04x image_destroy_attempted=%d "
            "image_destroy_egl_error=0x%04x source_texture=%u destination_texture=%u "
            "texture_ids_distinct=%d destination_fbo=%u gl_bind_attempted=%d "
            "gl_bind_error=0x%04x",
            record->format->label, (unsigned int) record->format->value, record->run,
            record->clientBufferAttempted ? 1 : 0, (void *) record->clientBuffer,
            record->clientBufferAttempted ? (record->clientBuffer ? "non_null" : "null")
                                           : "not_called",
            (unsigned int) record->clientBufferEglError,
            record->imageAttempted ? 1 : 0, record->imageCreated ? "non_null" :
                    (record->imageAttempted ? "null" : "not_called"),
            (unsigned int) record->imageCreateEglError,
            record->imageDestroyAttempted ? 1 : 0,
            (unsigned int) record->imageDestroyEglError,
            record->sourceTexture, record->destinationTexture,
            record->sourceTexture != 0 && record->destinationTexture != 0 &&
                    record->sourceTexture != record->destinationTexture ? 1 : 0,
            record->destinationFbo, record->glBindAttempted ? 1 : 0,
            (unsigned int) record->glBindError);

    GATEA_A1_LOG(
            "GATEA_A1 run format=%s format_value=%u run=%d "
            "vertex_compile_status=%d vertex_compile_gl_error=0x%04x vertex_compile_log=%s "
            "fragment_compile_status=%d fragment_compile_gl_error=0x%04x fragment_compile_log=%s "
            "shader_compile_status=%d link_status=%d link_gl_error=0x%04x link_log=%s "
            "position_location=%d texcoord_location=%d sampler_location=%d "
            "fbo_attempted=%d fbo_status=0x%04x fbo_gl_error=0x%04x",
            record->format->label, (unsigned int) record->format->value, record->run,
            record->vertexCompileStatus, (unsigned int) record->vertexCompileGlError,
            record->vertexCompileLog, record->fragmentCompileStatus,
            (unsigned int) record->fragmentCompileGlError, record->fragmentCompileLog,
            record->shaderCompileStatus, record->linkStatus,
            (unsigned int) record->linkGlError, record->linkLog,
            record->positionLocation, record->texcoordLocation, record->samplerLocation,
            record->fboAttempted ? 1 : 0, (unsigned int) record->fboStatus,
            (unsigned int) record->fboGlError);

    GATEA_A1_LOG(
            "GATEA_A1 run format=%s format_value=%u run=%d "
            "draw_attempted=%d draw_gl_error=0x%04x "
            "readback_attempted=%d readback_gl_error=0x%04x actual_rgba=%s "
            "comparison_valid=%d exact_fail_pixels=%u max_channel_delta=%u "
            "classification=%s details=%s",
            record->format->label, (unsigned int) record->format->value, record->run,
            record->drawAttempted ? 1 : 0, (unsigned int) record->drawGlError,
            record->readbackAttempted ? 1 : 0,
            (unsigned int) record->readbackGlError, actual,
            record->comparisonValid ? 1 : 0, record->exactFailPixels,
            record->maxChannelDelta, gateaA1ClassName(record->classification),
            record->details ? record->details : "none");
}

static void gateaA1LogAggregate(const unsigned int counts[3][GATEA_A1_CLASSIFICATION_COUNT]) {
#define GATEA_A1_COUNT_ARGS(index) \
        counts[index][GATEA_A1_ALLOC_FAIL], counts[index][GATEA_A1_LOCK_FAIL], \
        counts[index][GATEA_A1_EGL_CLIENT_BUFFER_FAIL], \
        counts[index][GATEA_A1_EGLIMAGE_CREATE_FAIL], counts[index][GATEA_A1_GL_BIND_FAIL], \
        counts[index][GATEA_A1_FBO_FAIL], counts[index][GATEA_A1_SHADER_COMPILE_FAIL], \
        counts[index][GATEA_A1_SHADER_LINK_FAIL], counts[index][GATEA_A1_SHADER_SAMPLE_EXACT], \
        counts[index][GATEA_A1_SHADER_SAMPLE_BLACK], \
        counts[index][GATEA_A1_SHADER_SAMPLE_CHANNEL_SWAP], \
        counts[index][GATEA_A1_SHADER_SAMPLE_ORIENTATION_ERROR], \
        counts[index][GATEA_A1_SHADER_SAMPLE_OTHER_MISMATCH], counts[index][GATEA_A1_UNLOCK_FAIL]

    GATEA_A1_LOG(
            "GATEA_A1 aggregate runs=9 "
            "control_format=%s control_value=%u control_counts="
            "ALLOC_FAIL:%u,LOCK_FAIL:%u,EGL_CLIENT_BUFFER_FAIL:%u,EGLIMAGE_CREATE_FAIL:%u,"
            "GL_BIND_FAIL:%u,FBO_FAIL:%u,SHADER_COMPILE_FAIL:%u,SHADER_LINK_FAIL:%u,"
            "SHADER_SAMPLE_EXACT:%u,SHADER_SAMPLE_BLACK:%u,SHADER_SAMPLE_CHANNEL_SWAP:%u,"
            "SHADER_SAMPLE_ORIENTATION_ERROR:%u,SHADER_SAMPLE_OTHER_MISMATCH:%u,UNLOCK_FAIL:%u "
            "bgra_format=%s bgra_value=%u bgra_counts="
            "ALLOC_FAIL:%u,LOCK_FAIL:%u,EGL_CLIENT_BUFFER_FAIL:%u,EGLIMAGE_CREATE_FAIL:%u,"
            "GL_BIND_FAIL:%u,FBO_FAIL:%u,SHADER_COMPILE_FAIL:%u,SHADER_LINK_FAIL:%u,"
            "SHADER_SAMPLE_EXACT:%u,SHADER_SAMPLE_BLACK:%u,SHADER_SAMPLE_CHANNEL_SWAP:%u,"
            "SHADER_SAMPLE_ORIENTATION_ERROR:%u,SHADER_SAMPLE_OTHER_MISMATCH:%u,UNLOCK_FAIL:%u "
            "rgba_format=%s rgba_value=%u rgba_counts="
            "ALLOC_FAIL:%u,LOCK_FAIL:%u,EGL_CLIENT_BUFFER_FAIL:%u,EGLIMAGE_CREATE_FAIL:%u,"
            "GL_BIND_FAIL:%u,FBO_FAIL:%u,SHADER_COMPILE_FAIL:%u,SHADER_LINK_FAIL:%u,"
            "SHADER_SAMPLE_EXACT:%u,SHADER_SAMPLE_BLACK:%u,SHADER_SAMPLE_CHANNEL_SWAP:%u,"
            "SHADER_SAMPLE_ORIENTATION_ERROR:%u,SHADER_SAMPLE_OTHER_MISMATCH:%u,UNLOCK_FAIL:%u",
            kGateaA1Formats[0].label, (unsigned int) kGateaA1Formats[0].value,
            GATEA_A1_COUNT_ARGS(0),
            kGateaA1Formats[1].label, (unsigned int) kGateaA1Formats[1].value,
            GATEA_A1_COUNT_ARGS(1),
            kGateaA1Formats[2].label, (unsigned int) kGateaA1Formats[2].value,
            GATEA_A1_COUNT_ARGS(2));
#undef GATEA_A1_COUNT_ARGS
}

extern "C" void gateaA1MicroprobeRun(EGLDisplay egl_display) {
    GateaA1GlState savedState;
    unsigned int counts[3][GATEA_A1_CLASSIFICATION_COUNT] = {};
    bool glReady;
    bool stateCaptured = false;
    size_t formatIndex;
    int run;

    glReady = egl_display != EGL_NO_DISPLAY &&
            eglGetCurrentDisplay() == egl_display &&
            eglGetCurrentContext() != EGL_NO_CONTEXT &&
            eglGetCurrentSurface(EGL_DRAW) != EGL_NO_SURFACE;
    if (glReady) {
        gateaA1ConsumeGlErrors();
        gateaA1CaptureGlState(&savedState);
        stateCaptured = true;
    }

    for (formatIndex = 0; formatIndex < 3; formatIndex++) {
        for (run = 1; run <= 3; run++) {
            GateaA1RunRecord record;
            gateaA1RunOne(egl_display, &kGateaA1Formats[formatIndex], run,
                          glReady, &record);
            if (record.classification < 0 ||
                record.classification >= GATEA_A1_CLASSIFICATION_COUNT) {
                record.classification = GATEA_A1_SHADER_SAMPLE_OTHER_MISMATCH;
                record.details = "classification_fallback";
            }
            counts[formatIndex][record.classification]++;
            gateaA1LogRun(&record);
        }
    }

    if (stateCaptured) {
        GLenum restoreError = gateaA1RestoreGlState(&savedState);
        GATEA_A1_LOG("GATEA_A1 state_restore_error=0x%04x",
                     (unsigned int) restoreError);
    }
    GATEA_A1_LOG("GATEA_A1 DIRECT_FBO_DIAGNOSTIC=SKIPPED");
    gateaA1LogAggregate(counts);
}
