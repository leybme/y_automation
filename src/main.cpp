#include <Arduino.h>

#include "config.h"
#include "io.h"
#include "persist.h"
#include "pin_control.h"
#include "tasks.h"

// ---------------------------------------------------------------------------
// Chip identification helper
// ---------------------------------------------------------------------------
#ifndef ARDUINO_ARCH_RP2040
static inline const char *chipModel() { return ESP.getChipModel(); }
#else
static inline const char *chipModel() {
#if defined(ARDUINO_RASPBERRY_PI_PICO2)
  return "RP2350";
#else
  return "RP2040";
#endif
}
#endif

void setup() {
  io::begin();
  pins::begin();
  persist::begin();

  // Boot defaults are applied before the reader accepts anything, so the
  // outputs are never briefly in an undefined state.
  int restored = persist::apply();

  io::raw("# %s %s proto=%s chip=%s", FW_NAME, FW_VERSION, FW_PROTO, chipModel());
  io::raw("# pinmask=0x%08X defaults=%d - send HELP for the command list",
          (unsigned)Y_PIN_MASK, restored);

  tasks::begin();

  io::raw("RDY");
}

void loop() {
#ifdef ARDUINO_ARCH_RP2040
  // RP2040/RP2350: no FreeRTOS, drive the reader cooperatively.
  tasks::poll();
#else
  // All work happens in the reader and worker tasks; keep the Arduino loop
  // task idle so it costs nothing.
  vTaskDelay(pdMS_TO_TICKS(1000));
#endif
}
