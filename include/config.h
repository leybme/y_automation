#pragma once

#include <stdint.h>

#define FW_NAME    "y_automation"
#define FW_VERSION "1.0.0"
#define FW_PROTO   "1"

#ifndef Y_SERIAL_BAUD
#define Y_SERIAL_BAUD 115200
#endif

// Number of GPIO slots in the pin-state array.
// Overridden per-board via -D Y_MAX_PINS=... in platformio.ini.
// Defaults to 22 (ESP32-C3: GPIO0..GPIO21).
#ifndef Y_MAX_PINS
#define Y_MAX_PINS 22
#endif

// Bit i set => GPIO i may be driven by the firmware.
// Default: GPIO0..GPIO10.  GPIO11..GPIO17 are wired to the SPI flash,
// GPIO18/GPIO19 are the native USB D-/D+ and GPIO20/GPIO21 are UART0.
// Override with -D Y_PIN_MASK=0x... in platformio.ini if your board differs.
#ifndef Y_PIN_MASK
#define Y_PIN_MASK 0x000007FFUL
#endif

// Serial reader limits.
#define Y_LINE_MAX   160
#define Y_MAX_TOKENS 8

// Depth of the reader -> worker command queue (FreeRTOS targets only).
#define Y_QUEUE_LEN 24

// ---------------------------------------------------------------------------
// LEDC / PWM configuration (not used on RP2040/RP2350).
// ---------------------------------------------------------------------------
#ifndef ARDUINO_ARCH_RP2040

// Maximum duty resolution supported by the LEDC hardware.
#define Y_LEDC_MAX_BITS 14

// Number of LEDC channels available.
// ESP32-C3: 6, ESP32-S3: 8, ESP32 (original): 16.
// Override per-board via -D Y_LEDC_CHANNELS=... in platformio.ini.
#ifndef Y_LEDC_CHANNELS
#define Y_LEDC_CHANNELS 6
#endif

// Source clock the duty resolution is budgeted against.
// ESP32-C3: 40 MHz crystal; ESP32 / ESP32-S3: 80 MHz APB.
// Override per-board via -D Y_LEDC_SRC_HZ=... in platformio.ini.
#ifndef Y_LEDC_SRC_HZ
#define Y_LEDC_SRC_HZ 40000000UL
#endif

#endif  // !ARDUINO_ARCH_RP2040

// Servo defaults (microseconds of pulse width at Y_SERVO_HZ).
#define Y_SERVO_HZ     50
#define Y_SERVO_MIN_US 500
#define Y_SERVO_MAX_US 2500

// Accepted range for the frequency generator.
#define Y_FREQ_MIN_HZ 1UL
#define Y_FREQ_MAX_HZ 10000000UL

// Task tuning (FreeRTOS targets only).
#define Y_READER_STACK 4096
#define Y_WORKER_STACK 4096
#define Y_READER_PRIO  3
#define Y_WORKER_PRIO  2
