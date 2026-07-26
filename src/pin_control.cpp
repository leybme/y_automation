#include "pin_control.h"

#include <Arduino.h>
#include <string.h>

#include "command.h"

// ---------------------------------------------------------------------------
// LEDC compatibility.  Arduino-ESP32 3.x drives LEDC per pin and allocates the
// channel itself; 2.x wants an explicit channel number.  Everything above this
// shim works in terms of pins only.
// ---------------------------------------------------------------------------
namespace {

#if ESP_ARDUINO_VERSION_MAJOR >= 3

bool ledcOpen(uint8_t pin, uint32_t hz, uint8_t bits, int8_t *channel) {
  *channel = 0;  // unused, the core keeps the mapping
  return ledcAttach(pin, hz, bits);
}

void ledcClose(uint8_t pin, int8_t *channel) {
  ledcDetach(pin);
  *channel = -1;
}

void ledcDuty(uint8_t pin, int8_t channel, uint32_t duty) {
  (void)channel;
  ledcWrite(pin, duty);
}

#else  // Arduino-ESP32 2.x

uint8_t g_channel_used = 0;  // bitmap of claimed LEDC channels

bool ledcOpen(uint8_t pin, uint32_t hz, uint8_t bits, int8_t *channel) {
  int8_t ch = -1;
  for (uint8_t i = 0; i < Y_LEDC_CHANNELS; ++i) {
    if (!(g_channel_used & (1u << i))) {
      ch = (int8_t)i;
      break;
    }
  }
  if (ch < 0) return false;
  if (ledcSetup((uint8_t)ch, (double)hz, bits) == 0) return false;
  ledcAttachPin(pin, (uint8_t)ch);
  g_channel_used |= (1u << ch);
  *channel = ch;
  return true;
}

void ledcClose(uint8_t pin, int8_t *channel) {
  ledcDetachPin(pin);
  if (*channel >= 0) g_channel_used &= ~(1u << *channel);
  *channel = -1;
}

void ledcDuty(uint8_t pin, int8_t channel, uint32_t duty) {
  if (channel >= 0) ledcWrite((uint8_t)channel, duty);
}

#endif

// Highest duty resolution the LEDC source clock can sustain at `hz`.
uint8_t resolutionFor(uint32_t hz) {
  uint8_t bits = Y_LEDC_MAX_BITS;
  while (bits > 1 && ((uint64_t)hz << bits) > (uint64_t)Y_LEDC_SRC_HZ) {
    bits--;
  }
  return bits;
}

PinState g_state[Y_MAX_PINS];
uint8_t  g_attached_count = 0;

// Tears down whatever the pin was doing and parks it as a floating input.
void teardown(uint8_t pin) {
  PinState &s = g_state[pin];
  if (s.attached) {
    ledcClose(pin, &s.channel);
    s.attached = false;
    s.attached_hz = 0;
    if (g_attached_count) g_attached_count--;
  }
  pinMode(pin, INPUT);
  s.func = PF_NONE;
  s.level = 0;
}

uint32_t servoDutyFor(uint16_t pulse_us, uint8_t bits) {
  const uint32_t period_us = 1000000UL / Y_SERVO_HZ;
  return (uint32_t)(((uint64_t)pulse_us << bits) / period_us);
}

uint32_t freqDutyFor(uint16_t duty_dp, uint8_t bits) {
  const uint32_t full = (1UL << bits) - 1UL;
  return (uint32_t)(((uint64_t)duty_dp * full) / 1000ULL);
}

// Brings a servo/frequency pin up at `hz`, reusing the channel when possible.
PinErr ledcEnsure(uint8_t pin, uint32_t hz) {
  PinState &s = g_state[pin];

  // Compare against what the hardware is actually running, not against the
  // requested value: callers may already have recorded their intent.
  if (s.attached && s.attached_hz == hz) return PE_OK;

  if (s.attached) {
    ledcClose(pin, &s.channel);
    s.attached = false;
    if (g_attached_count) g_attached_count--;
  } else if (g_attached_count >= Y_LEDC_CHANNELS) {
    return PE_NO_CHANNEL;
  }

  // Start from the best resolution the source clock should sustain and step
  // down until the driver accepts it, rather than trusting the estimate.
  for (uint8_t bits = resolutionFor(hz); bits >= 1; --bits) {
    if (ledcOpen(pin, hz, bits, &s.channel)) {
      s.attached = true;
      s.bits = bits;
      s.attached_hz = hz;
      g_attached_count++;
      return PE_OK;
    }
  }
  return PE_HW;
}

}  // namespace

namespace pins {

void begin() {
  memset(g_state, 0, sizeof(g_state));
  for (uint8_t p = 0; p < Y_MAX_PINS; ++p) {
    g_state[p].func = PF_NONE;
    g_state[p].channel = -1;
    g_state[p].attached_hz = 0;
    g_state[p].min_us = Y_SERVO_MIN_US;
    g_state[p].max_us = Y_SERVO_MAX_US;
    g_state[p].angle_dd = 900;
    g_state[p].duty_dp = 500;
    g_state[p].freq_hz = 1000;
  }
}

bool valid(uint8_t pin) {
  return pin < Y_MAX_PINS && ((Y_PIN_MASK >> pin) & 1UL) != 0;
}

const char *funcName(PinFunc f) {
  switch (f) {
    case PF_OUT:         return "OUT";
    case PF_IN:          return "IN";
    case PF_IN_PULLUP:   return "IN_PULLUP";
    case PF_IN_PULLDOWN: return "IN_PULLDOWN";
    case PF_SERVO:       return "SERVO";
    case PF_FREQ:        return "FREQ";
    default:             return "NONE";
  }
}

bool parseFunc(const char *s, PinFunc *out) {
  struct { const char *name; PinFunc f; } table[] = {
      {"NONE", PF_NONE},   {"OFF", PF_NONE},
      {"OUT", PF_OUT},     {"OUTPUT", PF_OUT},
      {"IN", PF_IN},       {"INPUT", PF_IN},
      {"IN_PULLUP", PF_IN_PULLUP},     {"PULLUP", PF_IN_PULLUP},
      {"IN_PULLDOWN", PF_IN_PULLDOWN}, {"PULLDOWN", PF_IN_PULLDOWN},
      {"SERVO", PF_SERVO},
      {"FREQ", PF_FREQ},   {"PWM", PF_FREQ},
  };
  for (auto &e : table) {
    if (strcmp(s, e.name) == 0) {
      *out = e.f;
      return true;
    }
  }
  return false;
}

const char *errName(PinErr e) {
  switch (e) {
    case PE_OK:         return "OK";
    case PE_BAD_PIN:    return "BAD_PIN";
    case PE_BAD_ARG:    return "BAD_ARG";
    case PE_WRONG_FUNC: return "WRONG_FUNC";
    case PE_NO_CHANNEL: return "NO_LEDC_CHANNEL";
    default:            return "HW_ERROR";
  }
}

const PinState &state(uint8_t pin) { return g_state[pin]; }

PinErr setFunc(uint8_t pin, PinFunc f) {
  if (!valid(pin)) return PE_BAD_PIN;
  PinState &s = g_state[pin];

  if (s.func == f) return PE_OK;
  if (s.func != PF_NONE) teardown(pin);

  switch (f) {
    case PF_NONE:
      return PE_OK;
    case PF_OUT:
      pinMode(pin, OUTPUT);
      digitalWrite(pin, LOW);
      s.level = 0;
      break;
    case PF_IN:
      pinMode(pin, INPUT);
      break;
    case PF_IN_PULLUP:
      pinMode(pin, INPUT_PULLUP);
      break;
    case PF_IN_PULLDOWN:
      pinMode(pin, INPUT_PULLDOWN);
      break;
    case PF_SERVO: {
      PinErr e = ledcEnsure(pin, Y_SERVO_HZ);
      if (e != PE_OK) return e;
      s.pulse_us = (uint16_t)(s.min_us +
                              ((uint32_t)(s.max_us - s.min_us) * s.angle_dd) / 1800UL);
      ledcDuty(pin, s.channel, servoDutyFor(s.pulse_us, s.bits));
      break;
    }
    case PF_FREQ: {
      PinErr e = ledcEnsure(pin, s.freq_hz);
      if (e != PE_OK) return e;
      ledcDuty(pin, s.channel, freqDutyFor(s.duty_dp, s.bits));
      break;
    }
  }
  s.func = f;
  return PE_OK;
}

PinErr writeDigital(uint8_t pin, int32_t level) {
  if (!valid(pin)) return PE_BAD_PIN;
  PinState &s = g_state[pin];

  if (s.func != PF_OUT) {
    PinErr e = setFunc(pin, PF_OUT);
    if (e != PE_OK) return e;
  }

  uint8_t next;
  if (level == LEVEL_TOGGLE) {
    next = s.level ? 0 : 1;
  } else if (level == LEVEL_LOW || level == LEVEL_HIGH) {
    next = (uint8_t)level;
  } else {
    return PE_BAD_ARG;
  }

  digitalWrite(pin, next ? HIGH : LOW);
  s.level = next;
  return PE_OK;
}

PinErr readDigital(uint8_t pin, int *out) {
  if (!valid(pin)) return PE_BAD_PIN;
  PinState &s = g_state[pin];

  // Reading an output reports what we are driving; anything not already an
  // input is switched to a plain input first.
  if (s.func == PF_SERVO || s.func == PF_FREQ) return PE_WRONG_FUNC;
  if (s.func == PF_NONE) {
    PinErr e = setFunc(pin, PF_IN);
    if (e != PE_OK) return e;
  }

  *out = digitalRead(pin) == HIGH ? 1 : 0;
  return PE_OK;
}

PinErr setServoAngle(uint8_t pin, int32_t angle_dd) {
  if (!valid(pin)) return PE_BAD_PIN;
  if (angle_dd < 0 || angle_dd > 1800) return PE_BAD_ARG;

  PinState &s = g_state[pin];
  s.angle_dd = (uint16_t)angle_dd;

  if (s.func != PF_SERVO) {
    PinErr e = setFunc(pin, PF_SERVO);
    if (e != PE_OK) return e;
  }

  s.pulse_us = (uint16_t)(s.min_us +
                          ((uint32_t)(s.max_us - s.min_us) * s.angle_dd) / 1800UL);
  ledcDuty(pin, s.channel, servoDutyFor(s.pulse_us, s.bits));
  return PE_OK;
}

PinErr setServoUs(uint8_t pin, int32_t us) {
  if (!valid(pin)) return PE_BAD_PIN;
  if (us < 100 || us > 19000) return PE_BAD_ARG;

  PinState &s = g_state[pin];
  if (s.func != PF_SERVO) {
    PinErr e = setFunc(pin, PF_SERVO);
    if (e != PE_OK) return e;
  }

  s.pulse_us = (uint16_t)us;
  if (us <= s.min_us) {
    s.angle_dd = 0;
  } else if (us >= s.max_us) {
    s.angle_dd = 1800;
  } else {
    s.angle_dd = (uint16_t)(((uint32_t)(us - s.min_us) * 1800UL) /
                            (uint32_t)(s.max_us - s.min_us));
  }

  ledcDuty(pin, s.channel, servoDutyFor(s.pulse_us, s.bits));
  return PE_OK;
}

PinErr setServoRange(uint8_t pin, int32_t min_us, int32_t max_us) {
  if (!valid(pin)) return PE_BAD_PIN;
  if (min_us < 100 || max_us > 19000 || min_us >= max_us) return PE_BAD_ARG;

  PinState &s = g_state[pin];
  s.min_us = (uint16_t)min_us;
  s.max_us = (uint16_t)max_us;

  if (s.func == PF_SERVO) return setServoAngle(pin, s.angle_dd);
  return PE_OK;
}

PinErr setFreq(uint8_t pin, uint32_t hz, int32_t duty_dp) {
  if (!valid(pin)) return PE_BAD_PIN;
  if (hz < Y_FREQ_MIN_HZ || hz > Y_FREQ_MAX_HZ) return PE_BAD_ARG;
  if (duty_dp < 0 || duty_dp > 1000) return PE_BAD_ARG;

  PinState &s = g_state[pin];
  if (s.func != PF_FREQ && s.func != PF_NONE) teardown(pin);

  PinErr e = ledcEnsure(pin, hz);
  if (e != PE_OK) return e;  // leave the recorded state alone on failure

  s.freq_hz = hz;
  s.duty_dp = (uint16_t)duty_dp;
  ledcDuty(pin, s.channel, freqDutyFor(s.duty_dp, s.bits));
  s.func = PF_FREQ;
  return PE_OK;
}

PinErr setDuty(uint8_t pin, int32_t duty_dp) {
  if (!valid(pin)) return PE_BAD_PIN;
  if (duty_dp < 0 || duty_dp > 1000) return PE_BAD_ARG;

  PinState &s = g_state[pin];
  if (s.func != PF_FREQ) return PE_WRONG_FUNC;

  s.duty_dp = (uint16_t)duty_dp;
  ledcDuty(pin, s.channel, freqDutyFor(s.duty_dp, s.bits));
  return PE_OK;
}

PinErr release(uint8_t pin) {
  if (!valid(pin)) return PE_BAD_PIN;
  teardown(pin);
  return PE_OK;
}

void allOff() {
  for (uint8_t p = 0; p < Y_MAX_PINS; ++p) {
    if (!valid(p)) continue;
    PinState &s = g_state[p];
    if (s.func == PF_OUT) {
      digitalWrite(p, LOW);
      s.level = 0;
    } else if (s.func != PF_NONE) {
      teardown(p);
    }
  }
}

}  // namespace pins
