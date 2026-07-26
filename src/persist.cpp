#include "persist.h"

#include <Arduino.h>
#include <Preferences.h>
#include <string.h>

#include "command.h"
#include "config.h"

namespace {

constexpr const char *kNamespace = "yauto";
constexpr const char *kBlobKey   = "pindefs";
constexpr uint8_t     kLayout    = 1;

struct Blob {
  uint8_t    layout;
  uint8_t    count;
  uint16_t   _pad;
  PinDefault pins[Y_MAX_PINS];
};

Preferences g_prefs;
Blob        g_blob;

void resetBlob() {
  memset(&g_blob, 0, sizeof(g_blob));
  g_blob.layout = kLayout;
  g_blob.count = Y_MAX_PINS;
  for (uint8_t p = 0; p < Y_MAX_PINS; ++p) {
    g_blob.pins[p].func = PF_NONE;
    g_blob.pins[p].min_us = Y_SERVO_MIN_US;
    g_blob.pins[p].max_us = Y_SERVO_MAX_US;
    g_blob.pins[p].angle_dd = 900;
    g_blob.pins[p].freq_hz = 1000;
    g_blob.pins[p].duty_dp = 500;
  }
}

bool store() {
  return g_prefs.putBytes(kBlobKey, &g_blob, sizeof(g_blob)) == sizeof(g_blob);
}

}  // namespace

namespace persist {

void begin() {
  resetBlob();
  if (!g_prefs.begin(kNamespace, false)) return;

  Blob tmp;
  size_t n = g_prefs.getBytes(kBlobKey, &tmp, sizeof(tmp));
  if (n == sizeof(tmp) && tmp.layout == kLayout && tmp.count == Y_MAX_PINS) {
    g_blob = tmp;
  } else if (n != 0) {
    // Stored by an older/newer layout: start clean rather than guess.
    store();
  }
}

const PinDefault &get(uint8_t pin) { return g_blob.pins[pin]; }

bool setDigital(uint8_t pin, uint8_t level) {
  if (!pins::valid(pin)) return false;
  PinDefault &d = g_blob.pins[pin];
  d.func = PF_OUT;
  d.level = level ? 1 : 0;
  return store();
}

bool snapshot() {
  for (uint8_t p = 0; p < Y_MAX_PINS; ++p) {
    if (!pins::valid(p)) continue;
    const PinState &s = pins::state(p);
    PinDefault &d = g_blob.pins[p];
    d.func = (uint8_t)s.func;
    d.level = s.level;
    d.angle_dd = s.angle_dd;
    d.min_us = s.min_us;
    d.max_us = s.max_us;
    d.freq_hz = s.freq_hz;
    d.duty_dp = s.duty_dp;
  }
  return store();
}

bool clear(uint8_t pin) {
  if (pin == PIN_NONE) {
    resetBlob();
  } else {
    if (!pins::valid(pin)) return false;
    g_blob.pins[pin].func = PF_NONE;
  }
  return store();
}

int apply() {
  int applied = 0;
  for (uint8_t p = 0; p < Y_MAX_PINS; ++p) {
    const PinDefault &d = g_blob.pins[p];
    if (d.func == PF_NONE || !pins::valid(p)) continue;

    PinErr e = PE_OK;
    switch ((PinFunc)d.func) {
      case PF_OUT:
        e = pins::writeDigital(p, d.level ? LEVEL_HIGH : LEVEL_LOW);
        break;
      case PF_SERVO:
        e = pins::setServoRange(p, d.min_us, d.max_us);
        if (e == PE_OK) e = pins::setServoAngle(p, d.angle_dd);
        break;
      case PF_FREQ:
        e = pins::setFreq(p, d.freq_hz, d.duty_dp);
        break;
      default:
        e = pins::setFunc(p, (PinFunc)d.func);
        break;
    }
    if (e == PE_OK) applied++;
  }
  return applied;
}

}  // namespace persist
