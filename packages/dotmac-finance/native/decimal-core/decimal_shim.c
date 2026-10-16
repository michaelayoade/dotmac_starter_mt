/* Quiet functions only: mpdec's default SIGFPE trap handler is never invoked. */
#include "decimal_shim.h"

#include <mpdecimal.h>
#include <stdlib.h>

static const int roundings[] = {
    MPD_ROUND_CEILING, MPD_ROUND_FLOOR, MPD_ROUND_UP, MPD_ROUND_DOWN,
    MPD_ROUND_HALF_UP, MPD_ROUND_HALF_DOWN, MPD_ROUND_HALF_EVEN, MPD_ROUND_05UP
};

static uint64_t wire_status(uint32_t status)
{
    uint32_t signals = 0;
    uint32_t conditions = 0;
    if (status & MPD_IEEE_Invalid_operation) signals |= 1U << 0;
    /* FloatOperation is a Python conversion condition, absent from this core. */
    if (status & MPD_Division_by_zero) signals |= 1U << 2;
    if (status & MPD_Overflow) signals |= 1U << 3;
    if (status & MPD_Underflow) signals |= 1U << 4;
    if (status & MPD_Subnormal) signals |= 1U << 5;
    if (status & MPD_Inexact) signals |= 1U << 6;
    if (status & MPD_Rounded) signals |= 1U << 7;
    if (status & MPD_Clamped) signals |= 1U << 8;
    if (status & MPD_Malloc_error) signals |= 1U << 9;
    if (status & MPD_Invalid_operation) conditions |= 1U << 0;
    if (status & MPD_Conversion_syntax) conditions |= 1U << 1;
    if (status & MPD_Division_impossible) conditions |= 1U << 2;
    if (status & MPD_Division_undefined) conditions |= 1U << 3;
    if (status & MPD_Invalid_context) conditions |= 1U << 4;
    return ((uint64_t)conditions << 32) | signals;
}

void *dm_context_new(int64_t precision, int rounding, int64_t emin,
                     int64_t emax, int clamp)
{
    mpd_context_t *context;
    if (precision < 1 || precision > MPD_MAX_PREC || rounding < 0 || rounding > 7 ||
        emin < MPD_MIN_EMIN || emin > 0 || emax < 0 || emax > MPD_MAX_EMAX ||
        clamp < 0 || clamp > 1) return NULL;
    context = malloc(sizeof(*context));
    if (context == NULL) return NULL;
    mpd_maxcontext(context);
    context->prec = (mpd_ssize_t)precision;
    context->round = roundings[rounding];
    context->emin = (mpd_ssize_t)emin;
    context->emax = (mpd_ssize_t)emax;
    context->clamp = clamp;
    context->traps = 0;
    return context;
}

void *dm_context_clone(const void *context)
{
    mpd_context_t *copy = malloc(sizeof(*copy));
    if (copy != NULL) *copy = *(const mpd_context_t *)context;
    return copy;
}

void dm_context_free(void *context) { free(context); }
void *dm_decimal_new(void) { return mpd_qnew(); }
void dm_decimal_free(void *decimal) { mpd_del(decimal); }

uint64_t dm_decimal_parse(void *decimal, const char *text)
{
    uint32_t status = 0;
    mpd_qset_string_exact(decimal, text, &status);
    return wire_status(status);
}

uint64_t dm_decimal_copy(void *result, const void *value)
{
    uint32_t status = 0;
    mpd_qcopy(result, value, &status);
    return wire_status(status);
}

#define BINARY(NAME, FUNC) \
uint64_t dm_decimal_##NAME(void *result, const void *left, const void *right, \
                           const void *context) \
{ \
    uint32_t status = 0; \
    FUNC(result, left, right, context, &status); \
    return wire_status(status); \
}

BINARY(add, mpd_qadd)
BINARY(sub, mpd_qsub)
BINARY(mul, mpd_qmul)
BINARY(div, mpd_qdiv)
BINARY(scaleb, mpd_qscaleb)

uint64_t dm_decimal_compare(const void *left, const void *right, int *order)
{
    uint32_t status = 0;
    *order = mpd_qcmp(left, right, &status);
    return wire_status(status);
}

uint64_t dm_decimal_quantize(void *result, const void *value,
                             const void *scale, const void *context, int rounding)
{
    uint32_t status = 0;
    mpd_context_t work = *(const mpd_context_t *)context;
    if (rounding < -1 || rounding > 7) return wire_status(MPD_Invalid_context);
    if (rounding >= 0) work.round = roundings[rounding];
    mpd_qquantize(result, value, scale, &work, &status);
    return wire_status(status);
}

char *dm_decimal_string(const void *decimal) { return mpd_to_sci(decimal, 1); }
void dm_string_free(char *text) { mpd_free(text); }
