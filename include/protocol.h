#pragma once

#include "command.h"

namespace protocol {

// Decodes one NUL terminated input line (modified in place).  Returns false
// and emits an ERR line itself when the input cannot be understood.
bool parse(char *line, Command *out);

// Runs a decoded command against the hardware and emits its reply.
void execute(const Command &cmd);

const char *verb(CmdType t);

}  // namespace protocol
