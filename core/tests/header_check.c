/* A C host of the library, compiled by tests/test_udeep_rust.py against include/udeep_core.h with warnings as
 * errors, and linked to the library cargo built. The header is written by hand; this is where a C compiler reads it.
 *
 * Standard input: a mapping record (range_low, range_high, knee, reference_headroom, stretch_width,
 * stretch_offset, exit_slope, stretch code), a count n, and n data values ("nan" for a NaN). Standard output: one
 * line for each thing asked of the library, for Python to compare with the golden vectors and the reference. */
#define _CRT_SECURE_NO_WARNINGS
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "udeep_core.h"

static void values(const char *name, int32_t status, const double *v, size_t n) {
    size_t i;
    printf("%s %d", name, (int)status);
    for (i = 0; i < n; i++) {
        if (isnan(v[i])) {
            printf(" nan");
        } else {
            printf(" %.17g", v[i]);
        }
    }
    printf("\n");
}

static udeep_text text(const char *s) {
    udeep_text t;
    t.data = s;
    t.length = s == NULL ? 0 : strlen(s);
    return t;
}

/* Text the library returns in memory of its own: printed, then released. */
static void owned(const char *name, int32_t (*f)(const udeep_declaration *, uint8_t **, size_t *),
                  const udeep_declaration *d) {
    uint8_t *data = NULL;
    size_t length = 0;
    int32_t status = f(d, &data, &length);
    printf("%s %d %.*s\n", name, (int)status, (int)length, data == NULL ? "" : (const char *)data);
    udeep_bytes_free(data, length);
}

int main(void) {
    udeep_mapping m;
    udeep_declaration d;
    unsigned int stretch;
    size_t n, i;
    double *x, *out, t[11], shaped[11];
    uint8_t block[UDEEP_ISO21496_LENGTH];
    char digest[UDEEP_DIGEST_LENGTH + 1];

    if (scanf("%lf %lf %lf %lf %lf %lf %lf %u %zu", &m.range_low, &m.range_high, &m.knee, &m.reference_headroom,
              &m.stretch_width, &m.stretch_offset, &m.exit_slope, &stretch, &n) != 9) {
        return 2;
    }
    m.stretch = stretch;
    m.reserved = 0;
    x = malloc(n * sizeof *x);
    out = malloc(n * sizeof *out);
    if (x == NULL || out == NULL) {
        return 3;
    }
    for (i = 0; i < n; i++) {
        if (scanf("%lf", &x[i]) != 1) {
            return 2;
        }
    }

    printf("sizes %zu %zu %zu %zu\n", sizeof(udeep_mapping), offsetof(udeep_mapping, stretch),
           sizeof(udeep_declaration), sizeof(udeep_text));
    printf("version %u\n", (unsigned int)udeep_version());
    printf("validate %d\n", (int)udeep_mapping_validate(&m));
    values("position", udeep_position(&m, x, out, n), out, n);
    values("light", udeep_light(&m, x, out, n), out, n);
    values("sdr_light", udeep_sdr_light(&m, x, out, n), out, n);

    for (i = 0; i < 11; i++) {
        t[i] = (double)i / 10.0;
    }
    values("tone", udeep_tone(0.8, 4.0, 1.0, t, shaped, 11), shaped, 11);
    printf("iso %d ", (int)udeep_iso21496_block(-0.0125, 2.0, 2.0, block));
    for (i = 0; i < UDEEP_ISO21496_LENGTH; i++) {
        printf("%02x", block[i]);
    }
    printf("\n");
    printf("digest %d %s\n", (int)udeep_digest_bytes((const uint8_t *)"abc", 3, digest), digest);

    /* the least a declaration can say: the reference's Declaration(data_unit="K", stretch="linear",
     * range_low=0, range_high=100, knee=50, knee_rule="preset", reference_headroom=2, max_data_step=0.4) */
    memset(&d, 0, sizeof d);
    d.mapping.range_low = 0.0;
    d.mapping.range_high = 100.0;
    d.mapping.knee = 50.0;
    d.mapping.reference_headroom = 2.0;
    d.mapping.stretch_width = 1.0;
    d.mapping.stretch_offset = 0.0;
    d.mapping.exit_slope = 1.0;
    d.mapping.stretch = UDEEP_STRETCH_LINEAR;
    d.max_data_step = 0.4;
    d.noise_parameter = NAN;
    d.rounding_to_noise = NAN;
    d.data_unit = text("K");
    d.knee_rule = text("preset");
    d.noise_model = text("unknown");
    d.record = text("rendering");
    owned("description", udeep_description, &d);
    owned("caption", udeep_caption, &d);

    free(x);
    free(out);
    return 0;
}
