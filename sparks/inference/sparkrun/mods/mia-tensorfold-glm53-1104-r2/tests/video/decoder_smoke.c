/* Exercise only the portable libtfvideo ABI. Never load CUDA or the serving binary. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct tf_video tf_video;
typedef struct { int64_t frames; int rate_num, rate_den, width, height, counted; } Info;
typedef int (*Frame)(void*, int, int, const uint8_t*, int, int, int);
extern const char* tf_video_version(void);
extern int tf_video_open(const uint8_t*, size_t, tf_video**, Info*);
extern int tf_video_frames(tf_video*, const int*, int, Frame, void*);
extern void tf_video_close(tf_video*);

static const int wanted[] = {0, 3, 7};
typedef struct { int seen, stop, bad; } Check;

static int take(void* ctx, int k, int index, const uint8_t* rgb, int w, int h, int stride) {
    Check* c = ctx;
    if (c->stop) return 1;
    if (k != c->seen || k >= 3 || index != wanted[k] || w != 64 || h != 48 || stride < w * 3) {
        c->bad = 1;
        return 1;
    }
    const int expected[] = {30 + 20 * index, 50 + 5 * index, 200 - 10 * index};
    for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) for (int ch = 0; ch < 3; ++ch) {
        /* A functional colour check against independently generated source pixels.
           This tolerance allows codec loss and is not a production pixel-parity claim. */
        if (abs((int)rgb[y * stride + x * 3 + ch] - expected[ch]) > 8) {
            c->bad = 1;
            return 1;
        }
    }
    ++c->seen;
    return 0;
}

int main(int argc, char** argv) {
    if (argc < 2 || strcmp(tf_video_version(), "9.0.2") != 0) return 1;
    for (int at = 1; at < argc; ++at) {
        FILE* f = fopen(argv[at], "rb");
        if (!f || fseek(f, 0, SEEK_END)) return 2;
        long len = ftell(f);
        if (len < 1 || len > (1 << 20) || fseek(f, 0, SEEK_SET)) return 3;
        uint8_t* bytes = malloc((size_t)len);
        if (!bytes || fread(bytes, 1, (size_t)len, f) != (size_t)len) return 4;
        fclose(f);
        tf_video* v = NULL;
        Info info;
        if (tf_video_open(bytes, (size_t)len, &v, &info) || !v || info.frames != 8 ||
            info.width != 64 || info.height != 48 || info.rate_den < 1 || info.rate_num != 4 * info.rate_den) return 5;
        for (int repeat = 0; repeat < 2; ++repeat) {
            Check c = {0};
            if (tf_video_frames(v, wanted, 3, take, &c) != 3 || c.bad || c.seen != 3) return 6;
        }
        Check stopped = {.stop = 1};
        if (tf_video_frames(v, wanted, 3, take, &stopped) != -7) return 7;
        Check after = {0};
        if (tf_video_frames(v, wanted, 3, take, &after) != 3 || after.bad || after.seen != 3) return 8;
        tf_video_close(v);
        free(bytes);
        printf("PASS %s: 8 frames, indexed RGB, repeated decode, stopped callback and retry\n", argv[at]);
    }
    uint8_t invalid[] = {0, 1, 2, 3};
    tf_video* v = NULL;
    Info info;
    if (tf_video_open(invalid, sizeof invalid, &v, &info) == 0 || v != NULL) return 9;
    puts("PASS invalid video refused; FFmpeg 9.0.2 portable decoder only");
    return 0;
}
