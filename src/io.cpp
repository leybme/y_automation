#include "io.h"

#include <Arduino.h>
#include <stdarg.h>
#include <stdio.h>

#include "config.h"

namespace {

#ifndef ARDUINO_ARCH_RP2040
SemaphoreHandle_t g_lock = nullptr;
#endif

void emit(uint32_t seq, const char *fmt, va_list ap) {
  char buf[Y_LINE_MAX + 32];
  int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  if (n < 0) return;

#ifndef ARDUINO_ARCH_RP2040
  if (g_lock) xSemaphoreTake(g_lock, portMAX_DELAY);
#endif
  if (seq) {
    Serial.print(seq);
    Serial.print(':');
  }
  Serial.println(buf);
#ifndef ARDUINO_ARCH_RP2040
  if (g_lock) xSemaphoreGive(g_lock);
#endif
}

}  // namespace

namespace io {

void begin() {
#ifndef ARDUINO_ARCH_RP2040
  g_lock = xSemaphoreCreateMutex();
#endif
  Serial.begin(Y_SERIAL_BAUD);

#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  // On the native USB CDC the stock settings let one write block for up to ~2 s
  // (100 ms x 20 retries) when the host stops draining the endpoint.  Bound it
  // so that dropping a reply beats stalling the worker.
  Serial.setTxTimeoutMs(20);
#endif

  // The USB CDC endpoint enumerates a moment after boot; give the host a
  // chance to attach so the banner is not lost.  Never block forever.
  uint32_t deadline = millis() + 1500;
  while (!Serial && millis() < deadline) {
    delay(10);
  }
}

void reply(uint32_t seq, const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  emit(seq, fmt, ap);
  va_end(ap);
}

void raw(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  emit(0, fmt, ap);
  va_end(ap);
}

}  // namespace io
