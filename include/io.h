#pragma once

#include <stdint.h>

#if defined(__GNUC__)
#define Y_PRINTF_FMT(fmt_index, first_arg) \
  __attribute__((format(printf, fmt_index, first_arg)))
#else
#define Y_PRINTF_FMT(fmt_index, first_arg)
#endif

// Serialised access to the USB serial port.  Every helper emits exactly one
// complete line, so replies from the reader and the worker task never
// interleave mid-line.
namespace io {

void begin();

// Emits "<seq>:<line>" when seq != 0, otherwise just "<line>".
void reply(uint32_t seq, const char *fmt, ...) Y_PRINTF_FMT(2, 3);

// Emits a line without a sequence prefix (banner, async notices).
void raw(const char *fmt, ...) Y_PRINTF_FMT(1, 2);

}  // namespace io
