#ifndef DOTMAC_INVENTORY_DECIMAL_SHIM_H
#define DOTMAC_INVENTORY_DECIMAL_SHIM_H

#include <stdint.h>

#if defined(__GNUC__)
#pragma GCC visibility push(hidden)
#endif

/* Opaque handles: no Rust dependency on mpdecimal struct layout or globals. */
void *dm_context_new(int64_t precision, int rounding, int64_t emin,
                     int64_t emax, int clamp);
void dm_context_free(void *context);
void *dm_decimal_new(void);
void dm_decimal_free(void *decimal);
uint32_t dm_decimal_parse(void *decimal, const char *text);
uint32_t dm_decimal_add(void *result, const void *left, const void *right,
                        const void *context);
uint32_t dm_decimal_quantize(void *result, const void *value,
                             const void *scale, const void *context);
char *dm_decimal_string(const void *decimal);
void dm_string_free(char *text);

#if defined(__GNUC__)
#pragma GCC visibility pop
#endif

#endif
