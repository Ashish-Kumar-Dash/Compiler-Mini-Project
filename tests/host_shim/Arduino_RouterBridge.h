// Host shim of Arduino_RouterBridge: notify prints "B <name> <args...>", provide_safe stores handlers.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct ShimBridge {
  void (*heartbeat)(uint32_t, uint32_t, uint32_t) = nullptr;
  void (*sim_set)(uint8_t, uint16_t) = nullptr;
  bool begin(unsigned long = 115200) { return true; }
  bool provide_safe(const char* name, void (*f)(uint32_t, uint32_t, uint32_t)) {
    if (strcmp(name, "heartbeat") == 0) heartbeat = f;
    return true;
  }
  bool provide_safe(const char* name, void (*f)(uint8_t, uint16_t)) {
    if (strcmp(name, "sim_set") == 0) sim_set = f;
    return true;
  }
  template <typename... A> void notify(const char* name, A... args) {
    printf("B %s", name);
    ((printf(" %llu", (unsigned long long)args)), ...);
    printf("\n");
  }
};
extern ShimBridge Bridge;
