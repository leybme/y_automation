#include "tasks.h"

#include <Arduino.h>

#include "command.h"
#include "config.h"
#include "io.h"
#include "protocol.h"

// ---------------------------------------------------------------------------
// Platform task model selection.
//
// ESP32 family  – FreeRTOS is always present.  The reader and worker run as
//                 two independent tasks sharing a command queue; this keeps
//                 the input path responsive even when a command takes a while.
//
// RP2040/RP2350 – The earlephilhower arduino-pico core does not expose
//                 FreeRTOS by default.  We use a simple cooperative model:
//                 tasks::begin() registers the reader as a loop() hook via
//                 tasks::poll(), which main.cpp calls from loop().  Because
//                 everything is single-threaded no queue or mutex is needed.
// ---------------------------------------------------------------------------

namespace {

// ---------------------------------------------------------------------------
// Shared line-accumulation state (used by both platforms)
// ---------------------------------------------------------------------------

char   g_line[Y_LINE_MAX + 1];
size_t g_len      = 0;
bool   g_overflow = false;

// Process one byte from the serial stream.  Executes a complete command
// immediately when a newline is seen (on RP2040) or enqueues it (on ESP32).
#ifdef ARDUINO_ARCH_RP2040
void processByte(int ch) {
  if (ch == '\r' || ch == '\n') {
    if (g_overflow) {
      io::reply(0, "ERR ? LINE_TOO_LONG");
      g_overflow = false;
      g_len = 0;
      return;
    }
    if (g_len == 0) return;

    g_line[g_len] = '\0';
    g_len = 0;

    Command cmd;
    if (!protocol::parse(g_line, &cmd)) return;
    protocol::execute(cmd);
    return;
  }

  if (g_len < Y_LINE_MAX) {
    g_line[g_len++] = (char)ch;
  } else {
    g_overflow = true;
  }
}
#endif  // ARDUINO_ARCH_RP2040

// ---------------------------------------------------------------------------
// ESP32 / FreeRTOS back-end
// ---------------------------------------------------------------------------
#ifndef ARDUINO_ARCH_RP2040

QueueHandle_t g_queue = nullptr;

void readerTask(void *) {
  char   line[Y_LINE_MAX + 1];
  size_t len      = 0;
  bool   overflow = false;

  for (;;) {
    while (Serial.available() > 0) {
      int ch = Serial.read();
      if (ch < 0) break;

      if (ch == '\r' || ch == '\n') {
        if (overflow) {
          io::reply(0, "ERR ? LINE_TOO_LONG");
          overflow = false;
          len = 0;
          continue;
        }
        if (len == 0) continue;

        line[len] = '\0';
        len = 0;

        Command cmd;
        if (!protocol::parse(line, &cmd)) continue;

        if (xQueueSend(g_queue, &cmd, 0) != pdTRUE) {
          io::reply(cmd.seq, "ERR %s QUEUE_FULL", protocol::verb(cmd.type));
        }
        continue;
      }

      if (len < Y_LINE_MAX) {
        line[len++] = (char)ch;
      } else {
        overflow = true;
      }
    }

    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

void workerTask(void *) {
  Command cmd;
  for (;;) {
    if (xQueueReceive(g_queue, &cmd, portMAX_DELAY) == pdTRUE) {
      protocol::execute(cmd);
    }
  }
}

#endif  // !ARDUINO_ARCH_RP2040

}  // namespace

namespace tasks {

void begin() {
#ifndef ARDUINO_ARCH_RP2040
  g_queue = xQueueCreate(Y_QUEUE_LEN, sizeof(Command));
  if (!g_queue) {
    io::raw("# FATAL queue allocation failed");
    return;
  }

  xTaskCreate(workerTask, "cmd_worker", Y_WORKER_STACK, nullptr, Y_WORKER_PRIO, nullptr);
  xTaskCreate(readerTask, "usb_reader", Y_READER_STACK, nullptr, Y_READER_PRIO, nullptr);
#endif
  // On RP2040 there is nothing to start here; poll() is called from loop().
}

#ifdef ARDUINO_ARCH_RP2040
void poll() {
  while (Serial.available() > 0) {
    int ch = Serial.read();
    if (ch >= 0) processByte(ch);
  }
}
#endif

}  // namespace tasks
