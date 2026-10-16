#ifndef DOTMAC_DECIMAL_CORE_SHIM_H
#define DOTMAC_DECIMAL_CORE_SHIM_H

#include <stdint.h>

#if defined(__GNUC__)
#pragma GCC visibility push(hidden)
#endif

/* Opaque handles; status packs signal bits low and invalid-condition bits high. */
void *dm_context_new(int64_t precision, int rounding, int64_t emin,
                     int64_t emax, int clamp);
void *dm_context_clone(const void *context);
void dm_context_free(void *context);
void *dm_decimal_new(void);
void dm_decimal_free(void *decimal);
uint64_t dm_decimal_parse(void *decimal, const char *text);
uint64_t dm_decimal_copy(void *result, const void *value);
uint64_t dm_decimal_add(void *result, const void *left, const void *right,
                        const void *context);
uint64_t dm_decimal_sub(void *result, const void *left, const void *right,
                        const void *context);
uint64_t dm_decimal_mul(void *result, const void *left, const void *right,
                        const void *context);
uint64_t dm_decimal_div(void *result, const void *left, const void *right,
                        const void *context);
uint64_t dm_decimal_scaleb(void *result, const void *left, const void *right,
                           const void *context);
uint64_t dm_decimal_compare(const void *left, const void *right, int *order);
uint64_t dm_decimal_quantize(void *result, const void *value,
                             const void *scale, const void *context, int rounding);
char *dm_decimal_string(const void *decimal);
void dm_string_free(char *text);

#if defined(__GNUC__)
#pragma GCC visibility pop
#endif

#endif
