#include "policy.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#if defined(__ARM_NEON) || defined(__ARM_NEON__)
#include <arm_neon.h>
#endif

_Static_assert(sizeof(DuckPolicy) == 197896 * sizeof(float), "weight layout changed");

int duck_policy_load(DuckPolicy *p, const char *path) {
    const uint32_t endian = 1;
    if (*(const unsigned char *)&endian != 1) return -1;
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    char magic[8];
    int ok = fread(magic, 1, 8, f) == 8 && !memcmp(magic, "DUCKMLP1", 8)
        && fread(p, sizeof(*p), 1, f) == 1 && fgetc(f) == EOF;
    fclose(f);
    if (!ok) return -1;
    for (int i = 0; i < 61; ++i)
        if (!isfinite(p->mean[i]) || !isfinite(p->divisor[i]) || p->divisor[i] <= 0) return -1;
    return 0;
}

static float dot(const float *w, const float *x, int n) {
    int i = 0;
    float sum = 0;
#if defined(__ARM_NEON) || defined(__ARM_NEON__)
    float32x4_t a = vdupq_n_f32(0), b = a, c = a, d = a;
    for (; i + 15 < n; i += 16) {
        a = vmlaq_f32(a, vld1q_f32(w+i), vld1q_f32(x+i));
        b = vmlaq_f32(b, vld1q_f32(w+i+4), vld1q_f32(x+i+4));
        c = vmlaq_f32(c, vld1q_f32(w+i+8), vld1q_f32(x+i+8));
        d = vmlaq_f32(d, vld1q_f32(w+i+12), vld1q_f32(x+i+12));
    }
    a = vaddq_f32(vaddq_f32(a,b),vaddq_f32(c,d));
    for (; i + 3 < n; i += 4) a = vmlaq_f32(a,vld1q_f32(w+i),vld1q_f32(x+i));
    float32x2_t s = vadd_f32(vget_low_f32(a),vget_high_f32(a));
    sum = vget_lane_f32(s,0) + vget_lane_f32(s,1);
#endif
    for (; i < n; ++i) sum += w[i] * x[i];
    return sum;
}

static void dense(const float *x, float *y, const float *w, const float *b,
                  int in, int out, int elu) {
    for (int j = 0; j < out; ++j) {
        float v = dot(w + j * in, x, in) + b[j];
        y[j] = elu && v < 0 ? expm1f(v) : v;
    }
}

int duck_policy_infer(const DuckPolicy *p, const float obs[61], float actions[14]) {
    float x[61], a[512], b[256], c[128];
    for (int i = 0; i < 61; ++i) {
        if (!isfinite(obs[i])) return -1;
        x[i] = (obs[i] - p->mean[i]) / p->divisor[i];
    }
    dense(x,a,p->w0,p->b0,61,512,1);
    dense(a,b,p->w1,p->b1,512,256,1);
    dense(b,c,p->w2,p->b2,256,128,1);
    dense(c,actions,p->w3,p->b3,128,14,0);
    for (int i = 0; i < 14; ++i) if (!isfinite(actions[i])) return -1;
    return 0;
}
