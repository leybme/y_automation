#pragma once

#include <stdint.h>

// One decoded request.  Trivially copyable so it can travel through a
// FreeRTOS queue by value (no heap, no ownership questions).
enum CmdType : uint8_t {
  CMD_NONE = 0,
  CMD_PING,
  CMD_ID,
  CMD_HELP,
  CMD_STATE,
  CMD_MODE,
  CMD_DOUT,
  CMD_DREAD,
  CMD_SERVO,
  CMD_SERVOUS,
  CMD_SERVOCFG,
  CMD_FREQ,
  CMD_DUTY,
  CMD_STOP,
  CMD_ALLOFF,
  CMD_DEF,
  CMD_DEFSNAP,
  CMD_DEFCLR,
  CMD_DEFGET,
  CMD_DEFAPPLY,
  CMD_REBOOT,
};

// Digital level argument encoding for CMD_DOUT.
enum : int32_t {
  LEVEL_LOW    = 0,
  LEVEL_HIGH   = 1,
  LEVEL_TOGGLE = 2,
};

struct Command {
  CmdType  type;
  uint8_t  pin;   // 0xFF when the command is not pin scoped
  int32_t  a;     // level / mode / angle(0.1 deg) / hz / us
  int32_t  b;     // duty(0.1 %) / max_us
  uint32_t seq;   // caller supplied tag, 0 when absent
};

#define PIN_NONE 0xFF
