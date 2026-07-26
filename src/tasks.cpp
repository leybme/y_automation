#include "tasks.h"

#include <Arduino.h>

#include "command.h"
#include "config.h"
#include "io.h"
#include "protocol.h"

namespace {

QueueHandle_t g_queue = nullptr;

// ---------------------------------------------------------------------------
// Task 1 - USB serial reader.
// Accumulates bytes into a line, hands the decoded command to the worker and
// goes straight back to reading.  It never touches the GPIOs itself, so a slow
// command can never stall the input path.
// ---------------------------------------------------------------------------
void readerTask(void *) {
  char   line[Y_LINE_MAX + 1];
  size_t len = 0;
  bool   overflow = false;

  for (;;) {
    while (Serial.available() > 0) {
      int ch = Serial.read();
      if (ch < 0) break;

      if (ch == '\r' || ch == '\n') {
        if (overflow) {
          // The sequence tag may itself have been lost in the truncation, so
          // this one goes out untagged.
          io::reply(0, "ERR ? LINE_TOO_LONG");
          overflow = false;
          len = 0;
          continue;
        }
        if (len == 0) continue;  // ignore empty lines and CRLF pairs

        line[len] = '\0';
        len = 0;

        Command cmd;
        if (!protocol::parse(line, &cmd)) continue;  // parse() reported the error

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

// ---------------------------------------------------------------------------
// Task 2 - command worker.
// Blocks on the queue, owns every GPIO change and emits the reply.  Being the
// only writer means pin state needs no additional locking.
// ---------------------------------------------------------------------------
void workerTask(void *) {
  Command cmd;
  for (;;) {
    if (xQueueReceive(g_queue, &cmd, portMAX_DELAY) == pdTRUE) {
      protocol::execute(cmd);
    }
  }
}

}  // namespace

namespace tasks {

void begin() {
  g_queue = xQueueCreate(Y_QUEUE_LEN, sizeof(Command));
  if (!g_queue) {
    io::raw("# FATAL queue allocation failed");
    return;
  }

  xTaskCreate(workerTask, "cmd_worker", Y_WORKER_STACK, nullptr, Y_WORKER_PRIO, nullptr);
  xTaskCreate(readerTask, "usb_reader", Y_READER_STACK, nullptr, Y_READER_PRIO, nullptr);
}

}  // namespace tasks
