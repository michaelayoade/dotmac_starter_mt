/* Quiet functions only: mpdec's default SIGFPE trap handler is never invoked. */
#include "decimal_shim.h"

#include <mpdecimal.h>
#include <stdlib.h>

static uint32_t signals(uint32_t status)
{
    uint32_t result = 0;
    if (status & MPD_IEEE_Invalid_operation) result |= 1U << 0;
    /* FloatOperation and DivisionByZero cannot occur in these operations. */
    if (status & MPD_Division_by_zero) result |= 1U << 2;
    if (status & MPD_Overflow) result |= 1U << 3;
    if (status & MPD_Underflow) result |= 1U << 4;
    if (status & MPD_Subnormal) result |= 1U << 5;
    if (status & MPD_Inexact) result |= 1U << 6;
    if (status & MPD_Rounded) result |= 1U << 7;
    if (status & MPD_Clamped) result |= 1U << 8;
    if (status & MPD_Malloc_error) result |= 1U << 9;
    return result;
}

void *dm_context_new(int64_t precision, int rounding, int64_t emin,
                     int64_t emax, int clamp)
{
    static const int roundings[] = {
        MPD_ROUND_CEILING, MPD_ROUND_FLOOR, MPD_ROUND_UP, MPD_ROUND_DOWN,
        MPD_ROUND_HALF_UP, MPD_ROUND_HALF_DOWN, MPD_ROUND_HALF_EVEN, MPD_ROUND_05UP
    };
    mpd_context_t *context = malloc(sizeof(*context));
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

void dm_context_free(void *context) { free(context); }
void *dm_decimal_new(void) { return mpd_qnew(); }
void dm_decimal_free(void *decimal) { mpd_del(decimal); }

uint32_t dm_decimal_parse(void *decimal, const char *text)
{
    uint32_t status = 0;
    mpd_qset_string_exact(decimal, text, &status);
    return signals(status);
}

uint32_t dm_decimal_add(void *result, const void *left, const void *right,
                        const void *context)
{
    uint32_t status = 0;
    mpd_qadd(result, left, right, context, &status);
    return signals(status);
}

uint32_t dm_decimal_quantize(void *result, const void *value,
                             const void *scale, const void *context)
{
    uint32_t status = 0;
    mpd_context_t work = *(const mpd_context_t *)context;
    work.round = MPD_ROUND_HALF_UP;
    mpd_qquantize(result, value, scale, &work, &status);
    return signals(status);
}

char *dm_decimal_string(const void *decimal) { return mpd_to_sci(decimal, 1); }
void dm_string_free(char *text) { mpd_free(text); }
