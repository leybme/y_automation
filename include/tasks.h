#pragma once

namespace tasks {

// Creates the command queue and starts the reader and worker tasks.
// On RP2040/RP2350 this is a no-op; call poll() from loop() instead.
void begin();

#ifdef ARDUINO_ARCH_RP2040
// Drain all pending serial bytes and execute any complete commands.
// Must be called repeatedly from loop().
void poll();
#endif

}  // namespace tasks
