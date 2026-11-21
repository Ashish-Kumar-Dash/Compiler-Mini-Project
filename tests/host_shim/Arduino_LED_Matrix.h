// Host shim of Arduino_LED_Matrix (frames are reported through iotc_host_frame instead).
#pragma once
#include <stdint.h>
struct Arduino_LED_Matrix {
  int begin() { return 1; }
  void setGrayscaleBits(uint8_t) {}
  void draw(const uint8_t*) {}
};
