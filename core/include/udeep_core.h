/* udeep core: the mapping from scientific data to light and colour (UDeep Figure Profile, draft v0.3).
 *
 *     value x  ->  position t in [0, 1]  ->  light y, in multiples of SDR white  ->  linear RGB, same unit
 *
 * Plain C. Every function works on buffers of n values, keeps no state, and may be called from several threads
 * at once. Each returns 0 on success or an UDEEP_ERROR_* code. Light is always in multiples of SDR white.
 *
 * This header is written by hand and must match core/src/ffi.rs; tests/test_udeep_rust.py calls every function
 * through it from Python and compares the results with the golden vectors.
 */
#ifndef UDEEP_CORE_H
#define UDEEP_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    UDEEP_OK = 0,
    UDEEP_ERROR_NULL_POINTER = 1,
    UDEEP_ERROR_UNKNOWN_STRETCH = 2,
    UDEEP_ERROR_BAD_RANGE = 3,          /* range_high <= range_low, or a log stretch starting at or below 0 */
    UDEEP_ERROR_KNEE_OUTSIDE_RANGE = 4, /* or within 1e-6 of the bottom, or of the top without being at it */
    UDEEP_ERROR_BAD_HEADROOM = 5,       /* reference_headroom < 1, or exit_slope outside 0..3 */
    UDEEP_ERROR_BAD_STRETCH_PARAMETER = 6, /* stretch_width <= 0, or a proportional mapping that is not a line */
    UDEEP_ERROR_BAD_TABLE = 7,
    UDEEP_ERROR_BAD_IMAGE = 8,          /* sizes that do not match, SDR outside 0..1, an infinite HDR value,
                                             headroom not above 1 */
    UDEEP_ERROR_CODEC = 9,              /* the image codec failed, a metadata segment was too large, or the gain
                                             map could not be kept within the headroom at this gain quality */
    UDEEP_ERROR_NOT_FINITE = 10,        /* a number in the mapping record, or a parameter, is NaN or infinite */
    UDEEP_ERROR_BAD_DECLARATION = 11    /* a required text missing or not UTF-8, an unknown noise model, a
                                             number that means nothing; or a declaration for a PQ file whose
                                             white is not at 203 cd/m^2 (profile 9.1.1) */
};

enum {
    UDEEP_STRETCH_LINEAR = 0,
    UDEEP_STRETCH_LOG = 1,
    UDEEP_STRETCH_ASINH = 2,
    UDEEP_STRETCH_PROPORTIONAL = 3      /* light = value / knee: range_low 0, no offset, exit_slope 1, and
                                             range_high = knee * reference_headroom (to a relative 1e-9) */
};

/* The mapping a file declares. Field names follow the udeep: metadata properties. 64 bytes.
 * The six numbers before exit_slope must be finite. The knee, less the offset, must lie above range_low and at or
 * below range_high, and its position in the range must be at least 1e-6 from the bottom and either exactly at the
 * top or at least 1e-6 below it. */
typedef struct udeep_mapping {
    double range_low;           /* the declared range, in data units, after stretch_offset is subtracted */
    double range_high;
    double knee;                /* the data value shown at SDR white */
    double reference_headroom;  /* light at the top of the range, in multiples of SDR white */
    double stretch_width;       /* asinh: width of the linear part; must be > 0 for every stretch */
    double stretch_offset;      /* subtracted from the data first */
    double exit_slope;          /* form above the knee; 1 is the profile's reference ("hermite; exit=1") */
    uint32_t stretch;           /* UDEEP_STRETCH_* */
    uint32_t reserved;          /* must be 0 */
} udeep_mapping;

/* major * 10000 + minor * 100 + patch */
uint32_t udeep_version(void);

int32_t udeep_mapping_validate(const udeep_mapping *mapping);

/* x and out may be the same buffer. */
int32_t udeep_position(const udeep_mapping *mapping, const double *x, double *out, size_t n);
int32_t udeep_light(const udeep_mapping *mapping, const double *x, double *out, size_t n);
int32_t udeep_sdr_light(const udeep_mapping *mapping, const double *x, double *out, size_t n);
int32_t udeep_value(const udeep_mapping *mapping, const double *light, double *out, size_t n);
/* Position in [0, 1] -> data values: the inverse of udeep_position inside the declared range. */
int32_t udeep_value_at(const udeep_mapping *mapping, const double *t, double *out, size_t n);

/* As udeep_light, on `threads` threads (0: every available core). The result is identical, bit for bit.
 * No thread is started when threads is 1, or when n is too small to be worth a second one (fewer than 32768
 * values): the loop then runs in the caller's thread, as udeep_light does. */
int32_t udeep_light_parallel(const udeep_mapping *mapping, const double *x, double *out, size_t n, size_t threads);

/* Light -> linear RGB (BT.709 primaries). table: 2 * nodes * 3 floats, the segment for light 0..1 then the one
 * for 1..headroom, or NULL for grey. rgb receives 3 * n values. */
int32_t udeep_colours(const float *table, size_t nodes, double headroom, const double *light, double *rgb, size_t n);
/* The same two on several threads (0: every available core), with identical results: see udeep_light_parallel.
 * light and rgb must not overlap. */
int32_t udeep_sdr_light_parallel(const udeep_mapping *mapping, const double *x, double *out, size_t n,
                                   size_t threads);
int32_t udeep_colours_parallel(const float *table, size_t nodes, double headroom, const double *light, double *rgb,
                                 size_t n, size_t threads);

/* Digests, as text: "sha256:" and 64 hex digits. out receives UDEEP_DIGEST_LENGTH characters and a zero. */
#define UDEEP_DIGEST_LENGTH 71

/* The digest of an array's values and shape (udeep:DataDigest). shape: `dimensions` sizes; values: their product
 * in doubles, row order. Independent of the type the data was stored as; every NaN equal; -0 counts as 0.
 * dimensions = 0 is one value with no shape (shape may then be NULL): it is digested as a list of one value,
 * shape 1, as the Python reference digests a scalar. */
int32_t udeep_digest_data(const size_t *shape, size_t dimensions, const double *values, char *out);

/* The digest of an 8-bit RGB image's pixels (udeep:SdrDigest): height * width * 3 bytes, row order. */
int32_t udeep_digest_pixels(size_t height, size_t width, const uint8_t *rgb, char *out);

/* ---- Drawing: the sRGB signal, and the one mask rule that looks at neighbours ----
 *
 * Linear light <-> sRGB signal, both in [0, 1] (values outside are clipped; NaN stays NaN). x and out may be the
 * same buffer. */
int32_t udeep_srgb_encode(const double *x, double *out, size_t n);
int32_t udeep_srgb_decode(const double *x, double *out, size_t n);

/* The isolated-3x3 rule (udeep:DerivedMask): of height rows of width bytes, row order, in which a non-zero byte is
 * a pixel above the knee, out gets 1 where such a pixel lies in no full 3 x 3 block of them, else 0. The buffers
 * must not overlap. */
int32_t udeep_mask_isolated(const uint8_t *above, size_t height, size_t width, uint8_t *out);

/* ---- The stages of the mapping on their own, and the bytes a file is checked by ----
 *
 * For a host that keeps its own mapping, a viewer that draws the curve, or a port that checks itself against the
 * golden vectors. Each refuses what the reference refuses, and first, unlike it, a parameter that is NaN or
 * infinite (UDEEP_ERROR_NOT_FINITE). x and out may be the same buffer.
 *
 * A bare scale: data values -> position in [0, 1]. kind: UDEEP_STRETCH_LINEAR, _LOG or _ASINH (a proportional
 * mapping is a linear scale). UDEEP_ERROR_UNKNOWN_STRETCH for another kind; UDEEP_ERROR_BAD_RANGE if hi <= lo or a
 * log scale has lo <= 0; UDEEP_ERROR_BAD_STRETCH_PARAMETER if width <= 0. */
int32_t udeep_scale(uint32_t kind, double lo, double hi, double width, const double *x, double *out, size_t n);
/* The tone curve: position -> light. knee in (0, 1] (UDEEP_ERROR_KNEE_OUTSIDE_RANGE); headroom >= 1 and exit_slope
 * in 0..3 (UDEEP_ERROR_BAD_HEADROOM). */
int32_t udeep_tone(double knee, double headroom, double exit_slope, const double *t, double *out, size_t n);
/* Luminance in cd/m^2 -> PQ signal in [0, 1] (SMPTE ST 2084); outside 0..10000 is clipped. */
int32_t udeep_pq_encode(const double *nits, double *out, size_t n);
/* The digest of any bytes, as the digests above (UDEEP_DIGEST_LENGTH characters and a zero): of a colour-map
 * table's file, the digest the index of the shipped tables records. data may be NULL when length is 0. */
int32_t udeep_digest_bytes(const uint8_t *data, size_t length, char *out);

/* The ISO 21496-1 gain-map metadata block a gain-map JPEG carries: UDEEP_ISO21496_LENGTH bytes. The three values
 * are log2, as the Ultra HDR XMP gives them, each stored over 1,000,000; UDEEP_ERROR_BAD_IMAGE if a numerator does
 * not fit the block (32-bit signed for the gains, unsigned for the capacity). */
#define UDEEP_ISO21496_LENGTH 61
int32_t udeep_iso21496_block(double gain_map_min, double gain_map_max, double hdr_capacity_max, uint8_t *out);

/* ---- What a file declares (profile section 8) ----
 *
 * Text passed in: length bytes of UTF-8 at data. A NULL data means the property is absent. */
typedef struct udeep_text {
    const char *data;
    size_t length;
} udeep_text;

/* The profile's version and image state are the library's own; the stretch and the form above the knee are taken
 * from the mapping record. data_unit, knee_rule, noise_model and record are required; the rest may be absent. */
typedef struct udeep_declaration {
    udeep_mapping mapping;
    double max_data_step;
    double noise_parameter;     /* NaN when the noise model is "unknown" */
    double rounding_to_noise;   /* NaN when the noise model is "unknown" */
    uint64_t masked;
    uint64_t derived_masked;
    udeep_text data_unit, knee_rule, noise_model, record;
    udeep_text derived_mask, preset, colour_map, colour_map_table, data_digest, data_source, data_unit_code,
        sdr_digest;
} udeep_declaration;

/* The declaration as the rdf:Description element a file carries, and the caption generated from it: UTF-8, not
 * zero-terminated, in memory owned by the library (release with udeep_bytes_free). */
int32_t udeep_description(const udeep_declaration *declaration, uint8_t **data, size_t *length);
int32_t udeep_caption(const udeep_declaration *declaration, uint8_t **data, size_t *length);

/* The stored levels of an 8-bit gain-map rendering: the widest (udeep:MaxDataStep) and the data criterion's worst
 * ratio (udeep:RoundingToNoise, at most 1e9). noise_model: 0 unknown (the ratio is then NaN), 1 constant,
 * 2 poisson, 3 relative, 4 tolerance. table: a colour-map table of `nodes` nodes per segment, or NULL for grey.
 * scaled_by: the headroom the file is written for. samples: positions looked at (the reference uses 65536). */
int32_t udeep_levels(const udeep_mapping *mapping, const float *table, size_t nodes, double scaled_by,
                       size_t samples, uint32_t noise_model, double noise_parameter, double *max_data_step,
                       double *rounding_to_noise);

/* As udeep_levels, for a 16-bit PQ file with SDR white at white_nits (the reference uses 203): one code for each
 * colour, and no split at the knee. samples should be about a million (the reference uses 1048576): such a file
 * has tens of thousands of levels. count, if not NULL, receives the number of levels found. */
int32_t udeep_levels_pq16(const udeep_mapping *mapping, const float *table, size_t nodes, double white_nits,
                            size_t samples, uint32_t noise_model, double noise_parameter, double *max_data_step,
                            double *rounding_to_noise, size_t *count);

/* Release bytes returned by this library. */
void udeep_bytes_free(uint8_t *data, size_t length);

/* ---- Only in a WebAssembly build (cargo build --release --target wasm32-unknown-unknown --features jpeg,png). ----
 *
 * Memory for a host whose buffers must lie inside the module's memory: size bytes, zeroed and aligned for a double,
 * or NULL when size is 0 or the memory is not to be had. Release with udeep_buffer_free and the same size. There
 * are no threads in such a build: the functions that take a number of threads run in the caller's, whatever is
 * asked for, with the same result. */
void *udeep_buffer_alloc(size_t size);
void udeep_buffer_free(void *data, size_t size);

/* ---- Only in a library built with the `jpeg` feature (the reader's two functions are at the end of this part) (cargo build --release --features jpeg,png). ----
 *
 * Write a gain-map JPEG (Ultra HDR layout, with the ISO 21496-1 block) into memory owned by the library.
 * sdr: linear RGB in 0..1; hdr: linear RGB in multiples of SDR white; each height * width * 3 doubles, row by row.
 * description: an rdf:Description element for the file's metadata, description_length bytes of UTF-8, or NULL and 0.
 * quality and gain_quality: 1..100 (the reference uses 95 and 100). On success *data and *length are the file.
 * A NaN in sdr or hdr is written as 0. An infinite value in hdr is refused (UDEEP_ERROR_BAD_IMAGE).
 *
 * A pixel the figure does not lift is stored with a gain of exactly 1 (GainMapMin is 0). Before the bytes are
 * returned the gain map is decoded again and lowered wherever the figure would read above the brightest value
 * of hdr, until nothing does; if that cannot be done the call fails with UDEEP_ERROR_CODEC and gives nothing.
 * Keep gain_quality at 100 unless the figure is grey: below it a gain map is unreliable with a colour map. */
int32_t udeep_gainmap_jpeg(const double *sdr, const double *hdr, size_t width, size_t height, double headroom,
                             uint32_t quality, uint32_t gain_quality, const char *description,
                             size_t description_length, uint8_t **data, size_t *length);


/* Reading a gain-map JPEG back (jpeg feature): its size, and the image as a display with display_boost times SDR
 * white of headroom shows it (1 gives the SDR base): height * width * 3 floats, linear RGB in multiples of SDR white,
 * row by row; n must be exactly that many. The decoder is the one the writer checks its files with.
 * UDEEP_ERROR_BAD_IMAGE: not a gain-map JPEG, or n is wrong. UDEEP_ERROR_CODEC: a gain map of another size than
 * its base, which this reader does not take. */
int32_t udeep_gainmap_size(const uint8_t *file, size_t length, size_t *width, size_t *height);
int32_t udeep_gainmap_read(const uint8_t *file, size_t length, double display_boost, float *out, size_t n,
                             size_t threads);

/* A figure's gain-map JPEG straight from four 8-bit renders of it (jpeg feature), each height * width * 3 sRGB
 * codes, row by row: the figure as it is (it becomes the file's SDR image unchanged); the same with each HDR image
 * divided by the headroom; and the same with the HDR images black, and white. A pixel that is black in the third and
 * white in the fourth is made by an HDR image alone, and only such a pixel is lifted (profile 6.11). The bytes come
 * back as from udeep_gainmap_jpeg; lifted receives the number of pixels above the ordinary figure. */
int32_t udeep_gainmap_from_renders(const uint8_t *base, const uint8_t *scaled, const uint8_t *black,
                                     const uint8_t *white, size_t width, size_t height, double headroom,
                                     uint32_t quality, uint32_t gain_quality, const char *description,
                                     size_t description_length, uint8_t **data, size_t *length, size_t *lifted);

/* ---- Only in a library built with the `png` feature (cargo build --release --features jpeg,png).
 *
 * A 16-bit PQ PNG (BT.2020 primaries, PQ, full range, declared in a cICP chunk) of rgb: height * width * 3 doubles,
 * row by row, linear RGB with BT.709 primaries in multiples of SDR white; 1.0 is written at white_nits. xmp: an
 * XMP packet to carry (the declaration), xmp_length bytes, or NULL and 0. A packet in the udeep namespace is
 * written only with white_nits at 203 (profile 9.1.1); at any other white, UDEEP_ERROR_BAD_DECLARATION. The bytes
 * come back in *data and *length (release them with udeep_bytes_free). A NaN is written as 0. This is the form in
 * which a file can be a data record (profile 7.3): see udeep_levels_pq16. */
int32_t udeep_png_pq(const double *rgb, size_t width, size_t height, double white_nits, const uint8_t *xmp,
                       size_t xmp_length, uint8_t **data, size_t *length);

/* Reading one back: its size, and its light, height * width * 3 doubles (linear RGB, BT.709 primaries, multiples of
 * the white written at white_nits), row by row; n must be exactly that many. Any of PNG's row filters is read.
 * UDEEP_ERROR_BAD_IMAGE: a PNG of another kind (not 16 bits a channel, not RGB, interlaced, or not declared
 * BT.2020 PQ), or n is wrong. UDEEP_ERROR_CODEC: not a whole PNG, or a checksum fails. */
int32_t udeep_png_pq_size(const uint8_t *file, size_t length, size_t *width, size_t *height);
int32_t udeep_png_pq_read(const uint8_t *file, size_t length, double white_nits, double *out, size_t n);

#ifdef __cplusplus
}
#endif

#endif /* UDEEP_CORE_H */
