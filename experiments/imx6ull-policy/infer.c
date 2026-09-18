#include "policy.h"
#include <stdio.h>
#include <stdlib.h>

/* Streaming interface for bench integrations: each request is 61 whitespace-
 * separated floats; each response is one line containing the 14 raw actions.
 * These are policy actions, not servo position commands. */
int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s weights.bin < observations.txt\n", argv[0]);
        return 2;
    }
    DuckPolicy *policy = malloc(sizeof(*policy));
    if (!policy || duck_policy_load(policy, argv[1])) {
        fprintf(stderr, "cannot load weights\n");
        free(policy);
        return 2;
    }
    for (;;) {
        float obs[61], actions[14];
        for (int i = 0; i < 61; ++i) {
            int read = scanf("%f", &obs[i]);
            if (read == EOF && i == 0 && !ferror(stdin)) {
                free(policy);
                return 0;
            }
            if (read != 1) {
                fprintf(stderr, "expected 61 floats per observation\n");
                free(policy);
                return 3;
            }
        }
        if (duck_policy_infer(policy, obs, actions)) {
            fprintf(stderr, "non-finite observation or action\n");
            free(policy);
            return 3;
        }
        for (int i = 0; i < 14; ++i)
            printf(i == 13 ? "%.9g\n" : "%.9g ", actions[i]);
        if (fflush(stdout) != 0) {
            free(policy);
            return 4;
        }
    }
}
