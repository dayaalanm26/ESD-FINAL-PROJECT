#ifndef GENERIC_DSO_SENDER_H
#define GENERIC_DSO_SENDER_H
#include <stddef.h>
#include <stdint.h>
/* Supply a blocking or buffered UART/USB writer that transmits all bytes. */
typedef void (*dso_write_fn)(const char *, size_t);
void dso_hello(dso_write_fn write);
/* Actual hardware timestamps (seconds) and raw ADC counts, one channel.
   Call outside the sampling ISR, from a completed timer/DMA buffer. */
void dso_samples(dso_write_fn write, uint32_t seq, const double *time_s,
                 const uint16_t *adc_counts, size_t count);
#endif
