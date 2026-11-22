// Host driver for the differential test (Architecture §13.3).
// stdin:  "M <passes_per_row>" once, then per row optional "H <seq> <mask> <ack>" lines followed by
//         "R <t_ms> <code0> <code1> ...". Heartbeats are delivered (millis = t) before the row's
//         first pass. With k passes per row, millis = t + j*S/k for pass j (mode A: k = 1).
// stdout: "E t a s src res det" / "L t up" / "N t rule" / "S t iter" (event hooks),
//         "F <104 digits>" (frame drawn), "O <actuator pin levels>" and "R t" after each row,
//         plus "B ..." Bridge notifies and "P ..." Serial lines.
#include <stdio.h>
#include <stdint.h>
#define IOTC_HOST_TEST 1
void iotc_host_event(char kind, uint32_t t, uint32_t a, uint32_t b, uint32_t c, uint32_t d, uint32_t e);
void iotc_host_frame(const uint8_t* px);
#include "Arduino.h"
uint32_t shim_millis = 0, shim_micros = 0;
int shim_pin_value[256], shim_pin_level[256], shim_pin_mode[256];
ShimSerial Serial;
#include "iotc_gen.h"
#if IOTC_BRIDGE
ShimBridge Bridge;
#endif

void iotc_host_event(char kind, uint32_t t, uint32_t a, uint32_t b, uint32_t c, uint32_t d, uint32_t e) {
  switch (kind) {
    case 'E': printf("E %u %u %u %u %u %u\n", t, a, b, c, d, e); break;
    case 'L': printf("L %u %u\n", t, a); break;
    case 'N': printf("N %u %u\n", t, a); break;
    case 'S': printf("S %u %u\n", t, a); break;
  }
}

void iotc_host_frame(const uint8_t* px) {
  printf("F ");
  for (int i = 0; i < 104; i++) printf("%u", px[i]);
  printf("\n");
}

int main() {
  int passes = 1;
  char kind;
  bool started = false;
  bool haveHb __attribute__((unused)) = false;
  unsigned long hb[3] = {0, 0, 0};
  while (scanf(" %c", &kind) == 1) {
    if (kind == 'M') { if (scanf("%d", &passes) != 1) return 2; continue; }
    if (kind == 'H') {
      if (scanf("%lu %lu %lu", &hb[0], &hb[1], &hb[2]) != 3) return 2;
      haveHb = true;
      continue;
    }
    if (kind != 'R') return 2;
    int t_row;
    if (scanf("%d", &t_row) != 1) return 2;
    uint32_t t = (uint32_t)t_row;
    for (int s = 0; s < iotc::N_SENS; s++) {
      int c;
      if (scanf("%d", &c) != 1) return 2;
      shim_pin_value[iotc::SENS_PIN[s] & 0xFF] = c;
    }
    shim_millis = t;
    shim_micros = t * 1000u;
    if (!started) { iotc::setup(); started = true; }
#if IOTC_BRIDGE
    if (haveHb && Bridge.heartbeat) Bridge.heartbeat((uint32_t)hb[0], (uint32_t)hb[1], (uint32_t)hb[2]);
#endif
    haveHb = false;
    (void)hb;
    for (int j = 0; j < passes; j++) {
      shim_millis = t + (uint32_t)(j * (int)iotc::SAMPLE_MS / passes);
      shim_micros = shim_millis * 1000u + (uint32_t)j;
      iotc::loop();
    }
    printf("O");
    for (int a = 0; a < iotc::N_ACT; a++) printf(" %d", shim_pin_level[iotc::ACT_PIN[a] & 0xFF]);
    printf("\nR %u\n", t);
    fflush(stdout);                     // the conformance test drives the shim interactively
  }
  return 0;
}
