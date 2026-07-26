#pragma once

#include <stdint.h>

#include "config.h"

// What a pin is currently wired up to do.
enum PinFunc : uint8_t {
  PF_NONE = 0,
  PF_OUT,
  PF_IN,
  PF_IN_PULLUP,
  PF_IN_PULLDOWN,
  PF_SERVO,
  PF_FREQ,
};

enum PinErr : uint8_t {
  PE_OK = 0,
  PE_BAD_PIN,
  PE_BAD_ARG,
  PE_WRONG_FUNC,
  PE_NO_CHANNEL,
  PE_HW,
};

struct PinState {
  PinFunc  func;
  uint8_t  level;          // last driven level for PF_OUT
  uint16_t angle_dd;       // servo angle in 0.1 deg (0..1800)
  uint16_t pulse_us;       // servo pulse width actually applied
  uint16_t min_us;         // servo pulse range
  uint16_t max_us;
  uint32_t freq_hz;        // PF_FREQ frequency, last one accepted by the driver
  uint16_t duty_dp;        // PF_FREQ duty in 0.1 % (0..1000)
  uint8_t  bits;           // LEDC resolution in use
  int8_t   channel;        // LEDC channel (Arduino core 2.x bookkeeping)
  bool     attached;       // LEDC currently driving the pin
  uint32_t attached_hz;    // frequency the hardware is actually configured for
};

namespace pins {

void begin();

bool         valid(uint8_t pin);
const char  *funcName(PinFunc f);
bool         parseFunc(const char *s, PinFunc *out);
const char  *errName(PinErr e);

const PinState &state(uint8_t pin);

PinErr setFunc(uint8_t pin, PinFunc f);
PinErr writeDigital(uint8_t pin, int32_t level);  // LEVEL_LOW/HIGH/TOGGLE
PinErr readDigital(uint8_t pin, int *out);
PinErr setServoAngle(uint8_t pin, int32_t angle_dd);
PinErr setServoUs(uint8_t pin, int32_t us);
PinErr setServoRange(uint8_t pin, int32_t min_us, int32_t max_us);
PinErr setFreq(uint8_t pin, uint32_t hz, int32_t duty_dp);
PinErr setDuty(uint8_t pin, int32_t duty_dp);
PinErr release(uint8_t pin);

void allOff();

}  // namespace pins
