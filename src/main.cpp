#include <Arduino.h>

#include "config.h"
#include "io.h"
#include "persist.h"
#include "pin_control.h"
#include "tasks.h"

void setup() {
  io::begin();
  pins::begin();
  persist::begin();

  // Boot defaults are applied before the reader accepts anything, so the
  // outputs are never briefly in an undefined state.
  int restored = persist::apply();

  io::raw("# %s %s proto=%s chip=%s", FW_NAME, FW_VERSION, FW_PROTO, ESP.getChipModel());
  io::raw("# pinmask=0x%08X defaults=%d - send HELP for the command list",
          (unsigned)Y_PIN_MASK, restored);

  tasks::begin();

  io::raw("RDY");
}

void loop() {
  // All work happens in the reader and worker tasks; keep the Arduino loop
  // task idle so it costs nothing.
  vTaskDelay(pdMS_TO_TICKS(1000));
}
