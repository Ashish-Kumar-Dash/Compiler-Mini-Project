// Host shim of the Arduino API used by generated sketches (Architecture §13.3). Test-only.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string>

typedef uint8_t pin_size_t;
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define A0 14
#define A1 15
#define A2 16
#define A3 17
#define A4 18
#define A5 19
#define LED3_R 100
#define LED3_G 101
#define LED3_B 102
#define LED4_R 103
#define LED4_G 104
#define LED4_B 105

extern uint32_t shim_millis;
extern uint32_t shim_micros;
extern int shim_pin_value[256];
extern int shim_pin_level[256];
extern int shim_pin_mode[256];

inline uint32_t millis() { return shim_millis; }
inline uint32_t micros() { return shim_micros; }
inline int analogRead(int pin) { return shim_pin_value[pin & 0xFF]; }
inline int digitalRead(int pin) { return shim_pin_value[pin & 0xFF] ? HIGH : LOW; }
inline void digitalWrite(int pin, int level) { shim_pin_level[pin & 0xFF] = level; }
inline void pinMode(int pin, int mode) { shim_pin_mode[pin & 0xFF] = mode; if (mode == OUTPUT) shim_pin_level[pin & 0xFF] = LOW; }
inline void noInterrupts() {}
inline void interrupts() {}

struct ShimSerial {
  std::string line;
  void begin(unsigned long) {}
  void print(const char* s) { line += s; }
  void print(char c) { line += c; }
  void print(int v) { line += std::to_string(v); }
  void print(unsigned int v) { line += std::to_string(v); }
  void print(long v) { line += std::to_string(v); }
  void print(unsigned long v) { line += std::to_string(v); }
  template <typename T> void println(T v) { print(v); printf("P %s\n", line.c_str()); line.clear(); }
};
extern ShimSerial Serial;
