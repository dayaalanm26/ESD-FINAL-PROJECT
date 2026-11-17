#include "generic_sender.h"
#include <stdio.h>
#include <string.h>

static void send_text(dso_write_fn write, const char *s) { write(s, strlen(s)); }
void dso_hello(dso_write_fn write) {
    /* Customize device, channel name, units and ADC range for your hardware. */
    send_text(write,"{\"type\":\"hello\",\"protocol\":1,\"device\":\"Custom 12-bit ADC\",\"channels\":[{\"name\":\"ADC0\",\"unit\":\"count\",\"min\":0,\"max\":4095}]}\n");
}
void dso_samples(dso_write_fn write, uint32_t seq, const double *time_s,
                 const uint16_t *adc_counts, size_t count) {
    char b[96];
    if (!write || !time_s || !adc_counts || !count || count > 4096) return;
    snprintf(b,sizeof b,"{\"type\":\"samples\",\"seq\":%lu,\"times\":[",(unsigned long)seq);
    send_text(write,b);
    for (size_t i=0;i<count;i++) {
        snprintf(b,sizeof b,"%s%.9f",i ? "," : "",time_s[i]); send_text(write,b);
    }
    send_text(write,"],\"values\":[");
    for (size_t i=0;i<count;i++) {
        snprintf(b,sizeof b,"%s[%u]",i ? "," : "",(unsigned)adc_counts[i]); send_text(write,b);
    }
    send_text(write,"]}\n");
}
