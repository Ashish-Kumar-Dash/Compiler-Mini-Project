// iotc Phase 0 serial probe: what does Serial do when the sketch never calls Bridge.begin()?
// Watch the board:
//   1. LED4 BLUE on for 2 s            -> sketch started
//   2. LED3 GREEN blinks at 2 Hz        -> Serial.begin + Serial.println per pass do NOT block
//      LED3 GREEN blinks slowly         -> each Serial call blocks (note the rate)
//      LED3 stays dark / LED4 stays on  -> Serial.begin hung or the sketch crashed
#include <Arduino.h>

static uint32_t lastToggle = 0;
static bool on = false;

void setup() {
  pinMode(LED4_B, OUTPUT);
  pinMode(LED3_G, OUTPUT);
  digitalWrite(LED3_G, HIGH);           // active-low: HIGH = off
  digitalWrite(LED4_B, LOW);            // blue on: alive
  uint32_t t0 = millis();
  while (millis() - t0 < 2000u) { }     // busy wait (setup only)
  digitalWrite(LED4_B, HIGH);
  Serial.begin(115200);
}

void loop() {
  Serial.println("iotc serial probe");
  uint32_t now = millis();
  if (now - lastToggle >= 250u) {
    lastToggle = now;
    on = !on;
    digitalWrite(LED3_G, on ? LOW : HIGH);
  }
}
