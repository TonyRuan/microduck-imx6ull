#ifndef MICRODUCK_IMX6ULL_POLICY_H
#define MICRODUCK_IMX6ULL_POLICY_H

/* Specialized FP32 executor for the validated v5 velstand graph, not a general
 * ONNX runtime. Observations/actions retain duck-control's 61/14 ordering. */
typedef struct {
    float mean[61], divisor[61];
    float w0[512 * 61], b0[512];
    float w1[256 * 512], b1[256];
    float w2[128 * 256], b2[128];
    float w3[14 * 128], b3[14];
} DuckPolicy;

int duck_policy_load(DuckPolicy *p, const char *path);
/* No allocation, file I/O, motor access, or shared mutable state during inference.
 * Returns -1 on non-finite input/output; caller must not use failed output. */
int duck_policy_infer(const DuckPolicy *p, const float obs[61], float actions[14]);
#endif
