#pragma once

#include <stdint.h>

#define FW_NAME    "y_automation"
#define FW_VERSION "1.0.0"
#define FW_PROTO   "1"

#ifndef Y_SERIAL_BAUD
#define Y_SERIAL_BAUD 115200
#endif

// ESP32-C3 exposes GPIO0..GPIO21.
#define Y_MAX_PINS 22

// Bit i set => GPIO i may be driven by the firmware.
// Default: GPIO0..GPIO10.  GPIO11..GPIO17 are wired to the SPI flash,
// GPIO18/GPIO19 are the native USB D-/D+ and GPIO20/GPIO21 are UART0.
// When using the USB-CDC build (esp32-c3-usbcdc), UART0 is not used for the
// serial interface so GPIO20/GPIO21 are available; that env sets
// Y_PIN_MASK=0x003007FFUL (GPIO0..GPIO10 + GPIO20..GPIO21).
// Override with -D Y_PIN_MASK=0x... in platformio.ini if your board differs.
#ifndef Y_PIN_MASK
#define Y_PIN_MASK 0x000007FFUL
#endif

// Serial reader limits.
#define Y_LINE_MAX   160
#define Y_MAX_TOKENS 8

// Depth of the reader -> worker command queue.
#define Y_QUEUE_LEN 24

// LEDC: the ESP32-C3 has 6 channels and a 14 bit maximum duty resolution.
#define Y_LEDC_MAX_BITS 14
#define Y_LEDC_CHANNELS 6

// Source clock the duty resolution is budgeted against.  Measured on hardware:
// the low speed LEDC timers run from the 40 MHz crystal, not the 80 MHz APB
// clock, and the driver rejects any request where hz * 2^bits exceeds it.
// Under-estimating this is safe (it only costs duty resolution), and
// ledcEnsure() steps the resolution down anyway if the driver still refuses.
#define Y_LEDC_SRC_HZ 40000000UL

// Servo defaults (microseconds of pulse width at Y_SERVO_HZ).
#define Y_SERVO_HZ     50
#define Y_SERVO_MIN_US 500
#define Y_SERVO_MAX_US 2500

// Accepted range for the frequency generator.
#define Y_FREQ_MIN_HZ 1UL
#define Y_FREQ_MAX_HZ 10000000UL

// Task tuning.
#define Y_READER_STACK 4096
#define Y_WORKER_STACK 4096
#define Y_READER_PRIO  3
#define Y_WORKER_PRIO  2
