#pragma once

#include <stdint.h>

#include "pin_control.h"

// Boot defaults, stored in NVS.  One record per GPIO; disabled records are
// skipped at boot so an unconfigured pin stays high impedance.
struct PinDefault {
  uint8_t  func;     // PinFunc, PF_NONE => record disabled
  uint8_t  level;    // PF_OUT level
  uint16_t angle_dd; // PF_SERVO angle in 0.1 deg
  uint16_t min_us;   // PF_SERVO pulse range
  uint16_t max_us;
  uint32_t freq_hz;  // PF_FREQ
  uint16_t duty_dp;  // PF_FREQ duty in 0.1 %
  uint16_t _pad;
};

namespace persist {

void begin();

const PinDefault &get(uint8_t pin);

// Store the boot level of a digital output pin.
bool setDigital(uint8_t pin, uint8_t level);

// Capture the live configuration of every configured pin.
bool snapshot();

// Erase one record, or every record when pin == PIN_NONE.
bool clear(uint8_t pin);

// Drive the hardware from the stored records.  Returns how many were applied.
int apply();

}  // namespace persist
