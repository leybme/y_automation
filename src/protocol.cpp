#include "protocol.h"

#include <Arduino.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "config.h"
#include "io.h"
#include "persist.h"
#include "pin_control.h"

namespace {

struct VerbEntry {
  const char *name;
  CmdType     type;
  uint8_t     min_args;  // arguments after the verb
  uint8_t     max_args;
};

const VerbEntry kVerbs[] = {
    {"PING",     CMD_PING,     0, 0},
    {"ID",       CMD_ID,       0, 0},
    {"HELP",     CMD_HELP,     0, 0},
    {"STATE",    CMD_STATE,    0, 1},
    {"MODE",     CMD_MODE,     2, 2},
    {"DOUT",     CMD_DOUT,     2, 2},
    {"DREAD",    CMD_DREAD,    1, 1},
    {"SERVO",    CMD_SERVO,    2, 2},
    {"SERVOUS",  CMD_SERVOUS,  2, 2},
    {"SERVOCFG", CMD_SERVOCFG, 3, 3},
    {"FREQ",     CMD_FREQ,     2, 3},
    {"DUTY",     CMD_DUTY,     2, 2},
    {"STOP",     CMD_STOP,     1, 1},
    {"ALLOFF",   CMD_ALLOFF,   0, 0},
    {"DEF",      CMD_DEF,      2, 2},
    {"DEFSNAP",  CMD_DEFSNAP,  0, 0},
    {"DEFCLR",   CMD_DEFCLR,   0, 1},
    {"DEFGET",   CMD_DEFGET,   0, 0},
    {"DEFAPPLY", CMD_DEFAPPLY, 0, 0},
    {"REBOOT",   CMD_REBOOT,   0, 0},
};

void err(uint32_t seq, const char *verb, const char *reason) {
  io::reply(seq, "ERR %s %s", verb, reason);
}

void upper(char *s) {
  for (; *s; ++s) *s = (char)toupper((unsigned char)*s);
}

// Parses a decimal number with optional fraction into a fixed point integer.
// scale 1 => "12" -> 12,  scale 10 => "90.5" -> 905 (extra digits truncated).
bool parseScaled(const char *s, int32_t scale, int32_t *out) {
  if (!s || !*s) return false;

  bool neg = false;
  if (*s == '+' || *s == '-') {
    neg = (*s == '-');
    ++s;
  }

  int64_t whole = 0;
  int64_t frac = 0;
  int64_t fscale = 1;
  bool any = false;

  while (*s >= '0' && *s <= '9') {
    whole = whole * 10 + (*s - '0');
    if (whole > 2000000000LL) return false;
    ++s;
    any = true;
  }
  if (*s == '.') {
    ++s;
    while (*s >= '0' && *s <= '9') {
      if (fscale < scale) {
        frac = frac * 10 + (*s - '0');
        fscale *= 10;
      }
      ++s;
      any = true;
    }
  }
  if (!any || *s != '\0') return false;

  int64_t v = whole * scale + (frac * scale) / fscale;
  if (v > 2000000000LL) return false;
  *out = (int32_t)(neg ? -v : v);
  return true;
}

bool parsePin(const char *s, uint8_t *out) {
  int32_t v;
  if (!parseScaled(s, 1, &v)) return false;
  if (v < 0 || v >= Y_MAX_PINS) return false;
  *out = (uint8_t)v;
  return true;
}

// Accepts 0/1 plus the friendlier spellings the GUI and humans use.
bool parseLevel(const char *s, int32_t *out) {
  if (strcmp(s, "0") == 0 || strcmp(s, "OFF") == 0 || strcmp(s, "LOW") == 0) {
    *out = LEVEL_LOW;
    return true;
  }
  if (strcmp(s, "1") == 0 || strcmp(s, "ON") == 0 || strcmp(s, "HIGH") == 0) {
    *out = LEVEL_HIGH;
    return true;
  }
  if (strcmp(s, "T") == 0 || strcmp(s, "TOGGLE") == 0) {
    *out = LEVEL_TOGGLE;
    return true;
  }
  return false;
}

void emitPin(uint32_t seq, uint8_t pin, const PinState &s) {
  switch (s.func) {
    case PF_OUT:
      io::reply(seq, "DAT pin=%u func=OUT level=%u", (unsigned)pin, (unsigned)s.level);
      break;
    case PF_SERVO:
      io::reply(seq, "DAT pin=%u func=SERVO angle=%u.%u us=%u min=%u max=%u",
                (unsigned)pin, (unsigned)(s.angle_dd / 10), (unsigned)(s.angle_dd % 10),
                (unsigned)s.pulse_us, (unsigned)s.min_us, (unsigned)s.max_us);
      break;
    case PF_FREQ:
      io::reply(seq, "DAT pin=%u func=FREQ hz=%u duty=%u.%u bits=%u",
                (unsigned)pin, (unsigned)s.freq_hz, (unsigned)(s.duty_dp / 10),
                (unsigned)(s.duty_dp % 10), (unsigned)s.bits);
      break;
    default:
      io::reply(seq, "DAT pin=%u func=%s level=%d", (unsigned)pin,
                pins::funcName(s.func), digitalRead(pin) == HIGH ? 1 : 0);
      break;
  }
}

void emitDefault(uint32_t seq, uint8_t pin, const PinDefault &d) {
  switch ((PinFunc)d.func) {
    case PF_OUT:
      io::reply(seq, "DAT def pin=%u func=OUT level=%u", (unsigned)pin, (unsigned)d.level);
      break;
    case PF_SERVO:
      io::reply(seq, "DAT def pin=%u func=SERVO angle=%u.%u min=%u max=%u",
                (unsigned)pin, (unsigned)(d.angle_dd / 10), (unsigned)(d.angle_dd % 10),
                (unsigned)d.min_us, (unsigned)d.max_us);
      break;
    case PF_FREQ:
      io::reply(seq, "DAT def pin=%u func=FREQ hz=%u duty=%u.%u", (unsigned)pin,
                (unsigned)d.freq_hz, (unsigned)(d.duty_dp / 10), (unsigned)(d.duty_dp % 10));
      break;
    default:
      io::reply(seq, "DAT def pin=%u func=%s", (unsigned)pin, pins::funcName((PinFunc)d.func));
      break;
  }
}

const char *kHelp[] = {
    "PING                        - liveness check",
    "ID                          - firmware identity",
    "STATE [pin]                 - dump live pin configuration",
    "MODE <pin> <func>           - NONE|OUT|IN|IN_PULLUP|IN_PULLDOWN|SERVO|FREQ",
    "DOUT <pin> <0|1|TOGGLE>     - drive a digital output",
    "DREAD <pin>                 - sample a digital input",
    "SERVO <pin> <deg>           - 0..180, one decimal accepted",
    "SERVOUS <pin> <us>          - raw pulse width",
    "SERVOCFG <pin> <min> <max>  - pulse range in us",
    "FREQ <pin> <hz> [duty%]     - square wave output, duty defaults to 50",
    "DUTY <pin> <duty%>          - change duty of a FREQ pin",
    "STOP <pin>                  - release a pin back to input",
    "ALLOFF                      - drive every output low, release PWM pins",
    "DEF <pin> <0|1>             - persist the boot level of an output",
    "DEFSNAP                     - persist the whole live configuration",
    "DEFCLR [pin]                - forget one or all boot defaults",
    "DEFGET                      - list stored boot defaults",
    "DEFAPPLY                    - re-apply stored boot defaults now",
    "REBOOT                      - restart the device",
};

}  // namespace

namespace protocol {

const char *verb(CmdType t) {
  for (const VerbEntry &e : kVerbs) {
    if (e.type == t) return e.name;
  }
  return "?";
}

bool parse(char *line, Command *out) {
  uint32_t seq = 0;

  while (*line == ' ' || *line == '\t') ++line;

  // Optional "<digits>:" correlation tag.
  char *colon = strchr(line, ':');
  if (colon) {
    bool digits = colon != line;
    for (char *p = line; p < colon; ++p) {
      if (*p < '0' || *p > '9') digits = false;
    }
    if (digits) {
      *colon = '\0';
      seq = (uint32_t)strtoul(line, nullptr, 10);
      line = colon + 1;
      while (*line == ' ' || *line == '\t') ++line;
    }
  }

  if (*line == '\0' || *line == '#') return false;  // blank line or comment

  // Split in place.  Deliberately not strtok(): that keeps static state, and
  // this parser should stay safe to call from more than one task.
  char *tok[Y_MAX_TOKENS];
  int   ntok = 0;
  for (char *p = line; *p && ntok < Y_MAX_TOKENS;) {
    while (*p == ' ' || *p == '\t' || *p == ',') ++p;
    if (*p == '\0') break;
    tok[ntok++] = p;
    while (*p && *p != ' ' && *p != '\t' && *p != ',') ++p;
    if (*p) *p++ = '\0';
  }
  if (ntok == 0) return false;

  upper(tok[0]);
  const VerbEntry *entry = nullptr;
  for (const VerbEntry &e : kVerbs) {
    if (strcmp(tok[0], e.name) == 0) {
      entry = &e;
      break;
    }
  }
  if (!entry) {
    err(seq, tok[0], "UNKNOWN_CMD");
    return false;
  }

  int nargs = ntok - 1;
  if (nargs < entry->min_args || nargs > entry->max_args) {
    err(seq, entry->name, "BAD_ARGC");
    return false;
  }

  Command c;
  memset(&c, 0, sizeof(c));
  c.type = entry->type;
  c.pin = PIN_NONE;
  c.seq = seq;

  // Every verb that takes a pin takes it first.
  bool wants_pin = (entry->type != CMD_PING && entry->type != CMD_ID &&
                    entry->type != CMD_HELP && entry->type != CMD_ALLOFF &&
                    entry->type != CMD_DEFSNAP && entry->type != CMD_DEFGET &&
                    entry->type != CMD_DEFAPPLY && entry->type != CMD_REBOOT);
  if (wants_pin && nargs >= 1) {
    if (!parsePin(tok[1], &c.pin)) {
      err(seq, entry->name, "BAD_PIN");
      return false;
    }
  }

  switch (entry->type) {
    case CMD_MODE: {
      upper(tok[2]);
      PinFunc f;
      if (!pins::parseFunc(tok[2], &f)) {
        err(seq, entry->name, "BAD_MODE");
        return false;
      }
      c.a = (int32_t)f;
      break;
    }
    case CMD_DOUT:
      upper(tok[2]);
      if (!parseLevel(tok[2], &c.a)) {
        err(seq, entry->name, "BAD_LEVEL");
        return false;
      }
      break;
    case CMD_DEF:
      upper(tok[2]);
      if (!parseLevel(tok[2], &c.a) || c.a == LEVEL_TOGGLE) {
        err(seq, entry->name, "BAD_LEVEL");
        return false;
      }
      break;
    case CMD_SERVO:
      if (!parseScaled(tok[2], 10, &c.a)) {
        err(seq, entry->name, "BAD_ANGLE");
        return false;
      }
      break;
    case CMD_SERVOUS:
      if (!parseScaled(tok[2], 1, &c.a)) {
        err(seq, entry->name, "BAD_US");
        return false;
      }
      break;
    case CMD_SERVOCFG:
      if (!parseScaled(tok[2], 1, &c.a) || !parseScaled(tok[3], 1, &c.b)) {
        err(seq, entry->name, "BAD_US");
        return false;
      }
      break;
    case CMD_FREQ:
      if (!parseScaled(tok[2], 1, &c.a)) {
        err(seq, entry->name, "BAD_HZ");
        return false;
      }
      c.b = 500;  // 50.0 % unless told otherwise
      if (nargs == 3 && !parseScaled(tok[3], 10, &c.b)) {
        err(seq, entry->name, "BAD_DUTY");
        return false;
      }
      break;
    case CMD_DUTY:
      if (!parseScaled(tok[2], 10, &c.a)) {
        err(seq, entry->name, "BAD_DUTY");
        return false;
      }
      break;
    default:
      break;
  }

  *out = c;
  return true;
}

void execute(const Command &cmd) {
  const char *v = verb(cmd.type);
  const uint32_t seq = cmd.seq;

  switch (cmd.type) {
    case CMD_PING:
      io::reply(seq, "OK PING PONG");
      break;

    case CMD_ID:
      io::reply(seq, "OK ID name=%s ver=%s proto=%s chip=%s pinmask=0x%08X", FW_NAME,
                FW_VERSION, FW_PROTO, ESP.getChipModel(), (unsigned)Y_PIN_MASK);
      break;

    case CMD_HELP: {
      for (const char *l : kHelp) io::reply(seq, "DAT %s", l);
      io::reply(seq, "OK HELP n=%u", (unsigned)(sizeof(kHelp) / sizeof(kHelp[0])));
      break;
    }

    case CMD_STATE: {
      unsigned n = 0;
      if (cmd.pin != PIN_NONE) {
        if (!pins::valid(cmd.pin)) {
          err(seq, v, "BAD_PIN");
          return;
        }
        emitPin(seq, cmd.pin, pins::state(cmd.pin));
        n = 1;
      } else {
        for (uint8_t p = 0; p < Y_MAX_PINS; ++p) {
          if (!pins::valid(p)) continue;
          const PinState &s = pins::state(p);
          if (s.func == PF_NONE) continue;
          emitPin(seq, p, s);
          n++;
        }
      }
      io::reply(seq, "OK STATE n=%u", n);
      break;
    }

    case CMD_MODE: {
      PinErr e = pins::setFunc(cmd.pin, (PinFunc)cmd.a);
      if (e != PE_OK) {
        err(seq, v, pins::errName(e));
        return;
      }
      io::reply(seq, "OK MODE pin=%u func=%s", (unsigned)cmd.pin,
                pins::funcName((PinFunc)cmd.a));
      break;
    }

    case CMD_DOUT: {
      PinErr e = pins::writeDigital(cmd.pin, cmd.a);
      if (e != PE_OK) {
        err(seq, v, pins::errName(e));
        return;
      }
      io::reply(seq, "OK DOUT pin=%u level=%u", (unsigned)cmd.pin,
                (unsigned)pins::state(cmd.pin).level);
      break;
    }

    case CMD_DREAD: {
      int level = 0;
      PinErr e = pins::readDigital(cmd.pin, &level);
      if (e != PE_OK) {
        err(seq, v, pins::errName(e));
        return;
      }
      io::reply(seq, "OK DREAD pin=%u level=%d", (unsigned)cmd.pin, level);
      break;
    }

    case CMD_SERVO:
    case CMD_SERVOUS: {
      PinErr e = (cmd.type == CMD_SERVO) ? pins::setServoAngle(cmd.pin, cmd.a)
                                         : pins::setServoUs(cmd.pin, cmd.a);
      if (e != PE_OK) {
        err(seq, v, pins::errName(e));
        return;
      }
      const PinState &s = pins::state(cmd.pin);
      io::reply(seq, "OK %s pin=%u angle=%u.%u us=%u", v, (unsigned)cmd.pin,
                (unsigned)(s.angle_dd / 10), (unsigned)(s.angle_dd % 10),
                (unsigned)s.pulse_us);
      break;
    }

    case CMD_SERVOCFG: {
      PinErr e = pins::setServoRange(cmd.pin, cmd.a, cmd.b);
      if (e != PE_OK) {
        err(seq, v, pins::errName(e));
        return;
      }
      io::reply(seq, "OK SERVOCFG pin=%u min=%d max=%d", (unsigned)cmd.pin, (int)cmd.a,
                (int)cmd.b);
      break;
    }

    case CMD_FREQ: {
      PinErr e = pins::setFreq(cmd.pin, (uint32_t)cmd.a, cmd.b);
      if (e != PE_OK) {
        err(seq, v, pins::errName(e));
        return;
      }
      const PinState &s = pins::state(cmd.pin);
      io::reply(seq, "OK FREQ pin=%u hz=%u duty=%u.%u bits=%u", (unsigned)cmd.pin,
                (unsigned)s.freq_hz, (unsigned)(s.duty_dp / 10), (unsigned)(s.duty_dp % 10),
                (unsigned)s.bits);
      break;
    }

    case CMD_DUTY: {
      PinErr e = pins::setDuty(cmd.pin, cmd.a);
      if (e != PE_OK) {
        err(seq, v, pins::errName(e));
        return;
      }
      const PinState &s = pins::state(cmd.pin);
      io::reply(seq, "OK DUTY pin=%u duty=%u.%u", (unsigned)cmd.pin,
                (unsigned)(s.duty_dp / 10), (unsigned)(s.duty_dp % 10));
      break;
    }

    case CMD_STOP: {
      PinErr e = pins::release(cmd.pin);
      if (e != PE_OK) {
        err(seq, v, pins::errName(e));
        return;
      }
      io::reply(seq, "OK STOP pin=%u", (unsigned)cmd.pin);
      break;
    }

    case CMD_ALLOFF:
      pins::allOff();
      io::reply(seq, "OK ALLOFF");
      break;

    case CMD_DEF:
      if (!pins::valid(cmd.pin)) {
        err(seq, v, "BAD_PIN");
        return;
      }
      if (!persist::setDigital(cmd.pin, (uint8_t)cmd.a)) {
        err(seq, v, "NVS_WRITE");
        return;
      }
      io::reply(seq, "OK DEF pin=%u level=%d", (unsigned)cmd.pin, (int)cmd.a);
      break;

    case CMD_DEFSNAP:
      if (!persist::snapshot()) {
        err(seq, v, "NVS_WRITE");
        return;
      }
      io::reply(seq, "OK DEFSNAP");
      break;

    case CMD_DEFCLR:
      if (!persist::clear(cmd.pin)) {
        err(seq, v, "NVS_WRITE");
        return;
      }
      if (cmd.pin == PIN_NONE) {
        io::reply(seq, "OK DEFCLR all");
      } else {
        io::reply(seq, "OK DEFCLR pin=%u", (unsigned)cmd.pin);
      }
      break;

    case CMD_DEFGET: {
      unsigned n = 0;
      for (uint8_t p = 0; p < Y_MAX_PINS; ++p) {
        if (!pins::valid(p)) continue;
        const PinDefault &d = persist::get(p);
        if (d.func == PF_NONE) continue;
        emitDefault(seq, p, d);
        n++;
      }
      io::reply(seq, "OK DEFGET n=%u", n);
      break;
    }

    case CMD_DEFAPPLY:
      io::reply(seq, "OK DEFAPPLY n=%d", persist::apply());
      break;

    case CMD_REBOOT:
      io::reply(seq, "OK REBOOT");
      delay(100);
      ESP.restart();
      break;

    default:
      err(seq, v, "UNHANDLED");
      break;
  }
}

}  // namespace protocol
