#include "io.h"

#include <Arduino.h>
#include <stdarg.h>
#include <stdio.h>

#include "config.h"

namespace {

SemaphoreHandle_t g_lock = nullptr;

void emit(uint32_t seq, const char *fmt, va_list ap) {
  char buf[Y_LINE_MAX + 32];
  int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  if (n < 0) return;

  if (g_lock) xSemaphoreTake(g_lock, portMAX_DELAY);
  if (seq) {
    Serial.print(seq);
    Serial.print(':');
  }
  Serial.println(buf);
  if (g_lock) xSemaphoreGive(g_lock);
}

}  // namespace

namespace io {

void begin() {
  g_lock = xSemaphoreCreateMutex();
  Serial.begin(Y_SERIAL_BAUD);

#if ARDUINO_USB_CDC_ON_BOOT
  // On the native USB CDC a host that stops draining the endpoint would
  // otherwise block each write for ~2 s (100 ms x 20 retries).  That stalls the
  // worker while it holds the output mutex, the command queue backs up, and the
  // reader stops draining USB RX as well - the whole firmware appears hung
  // until the host comes back.  Bound the wait so output is dropped instead.
  Serial.setTxTimeoutMs(20);
#endif
  // The native USB CDC endpoint enumerates a moment after boot; give the host
  // a chance to attach so the banner is not lost.  Never block forever, the
  // firmware has to run headless too.
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
