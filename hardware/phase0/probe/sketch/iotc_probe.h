// iotc Phase 0 probe — MCU side (hand-written; Architecture.md §15.1).
// Being a separate .h also tests that App Lab sketches accept extra header files.
//
// What it measures (sent to Linux with Bridge.notify; nothing is printed in loop() except the
// one-off Serial test at t = 25 s):
//   * loop pass interval (min / median / max / mean, us) with the Bridge linked
//   * micros() resolution, LED-matrix draw() cost, floating-A0 ADC noise
//   * whether pinMode(OUTPUT) keeps a level written before it (boot-glitch question)
//   * heartbeat handler context: main-thread stack vs another stack, and during loop() or not
//   * Bridge round trip (Linux measures it from p_hb -> p_ack)
//   * Python->MCU argument type matrix (u8/u16/u32/bool/float)
//   * MCU->Python 16-value message, and the pass stall caused by a ~2 KB notify burst
//   * cost of one Serial.begin() / Serial.println() (Serial is the RouterBridge Monitor)
// Visible on the board: LED3 then LED4 cycle red -> green -> blue (active-low), the LED matrix
// shows a moving dot over a brightness gradient.
#pragma once
#include <Arduino.h>
#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>

namespace probe {

static Arduino_LED_Matrix matrix;
static const uint8_t N_LED = 6;
static pin_size_t ledPins[N_LED];

// ---- handler globals (written by Bridge handlers, read in loop under noInterrupts) ----------
static volatile uint32_t rxSeq = 0, rxMask = 0, rxAck = 0, rxBeatMs = 0;
static volatile bool rxFresh = false;
static volatile uint32_t hbTotal = 0, hbMainStack = 0, hbOtherStack = 0, hbDuringLoop = 0;
static volatile uint32_t hbDiffMin = 0xFFFFFFFFu, hbDiffMax = 0;
static volatile bool inLoop = false;
static uintptr_t mainStackRef = 0;

static const uint8_t N_ECHO = 5;  // 0 u8, 1 u16, 2 u32, 3 bool, 4 float*1000
static volatile uint32_t echoValue[N_ECHO];
static volatile bool echoFlag[N_ECHO];

static uint32_t stackDistance() {
  volatile int marker = 0;
  uintptr_t here = (uintptr_t)&marker;
  return (uint32_t)(here > mainStackRef ? here - mainStackRef : mainStackRef - here);
}

static void onHb(uint32_t seq, uint32_t mask, uint32_t ack) {
  uint32_t d = stackDistance();
  bool during = inLoop;
  noInterrupts();
  rxSeq = seq; rxMask = mask; rxAck = ack; rxBeatMs = millis(); rxFresh = true;
  hbTotal++;
  if (d < 16384u) hbMainStack++; else hbOtherStack++;
  if (during) hbDuringLoop++;
  if (d < hbDiffMin) hbDiffMin = d;
  if (d > hbDiffMax) hbDiffMax = d;
  interrupts();
}

static void setEcho(uint8_t i, uint32_t v) {
  noInterrupts(); echoValue[i] = v; echoFlag[i] = true; interrupts();
}
static void onU8(uint8_t v) { setEcho(0, v); }
static void onU16(uint16_t v) { setEcho(1, v); }
static void onU32(uint32_t v) { setEcho(2, v); }
static void onBool(bool v) { setEcho(3, v ? 1u : 0u); }
static void onFloat(float v) { setEcho(4, (uint32_t)(v * 1000.0f + 0.5f)); }

// ---- measurements ---------------------------------------------------------------------------
static bool bridgeOk = false;
static uint32_t microsRes = 0, matrixDrawX10 = 0, matrixDrawMax = 0;
static int adcMin = 100000, adcMax = -100000, adcFaults = 0;
static uint32_t pinmodeKeepsLevel = 2;  // 2 = not measured
static uint32_t burstPassUs = 0, serialBeginUs = 0, serialPrintUs = 0;
static bool burstDone = false, serialDone = false;
static uint32_t startMs = 0;

static const uint16_t HIST_BINS = 100, HIST_W = 100;  // 100 us bins, 0..10 ms
static uint32_t hist[HIST_BINS + 1];
static uint32_t passCount = 0, passMin = 0xFFFFFFFFu, passMax = 0;
static uint64_t passSum = 0;
static uint32_t prevUs = 0;
static bool havePrev = false;

static void recordPass(uint32_t dt) {
  passCount++; passSum += dt;
  if (dt < passMin) passMin = dt;
  if (dt > passMax) passMax = dt;
  uint32_t b = dt / HIST_W;
  hist[b < HIST_BINS ? b : HIST_BINS]++;
}

static uint32_t histMedian() {
  uint32_t half = passCount / 2, acc = 0;
  for (uint16_t i = 0; i <= HIST_BINS; i++) {
    acc += hist[i];
    if (acc > half) return (uint32_t)i * HIST_W + HIST_W / 2;
  }
  return 0;
}

static void resetPassStats() {
  passCount = 0; passMin = 0xFFFFFFFFu; passMax = 0; passSum = 0;
  for (uint16_t i = 0; i <= HIST_BINS; i++) hist[i] = 0;
}

// ---- LED matrix -----------------------------------------------------------------------------
static uint8_t frame[104];
static uint32_t lastFrameMs = 0, dotPos = 0;

static void drawTimed() {
  uint32_t t0 = micros();
  matrix.draw(frame);
  uint32_t dt = micros() - t0;
  if (dt > matrixDrawMax) matrixDrawMax = dt;
}

static void buildFrame(uint32_t dot) {
  for (uint8_t i = 0; i < 104; i++) {
    uint8_t col = i % 13;
    frame[i] = (i / 13 == 7) ? (uint8_t)(col % 8) : 0;  // bottom row: brightness gradient
  }
  frame[dot % 91] = 7;                                      // moving dot in rows 0..6
}

// ---- setup / loop ---------------------------------------------------------------------------
static void setup() {
  volatile int ref = 0;
  mainStackRef = (uintptr_t)&ref;
  startMs = millis();

  ledPins[0] = LED3_R; ledPins[1] = LED3_G; ledPins[2] = LED3_B;
  ledPins[3] = LED4_R; ledPins[4] = LED4_G; ledPins[5] = LED4_B;
  // Boot-glitch question: is a level written before pinMode(OUTPUT) kept?
  digitalWrite(ledPins[0], HIGH);
  pinMode(ledPins[0], OUTPUT);
  pinmodeKeepsLevel = digitalRead(ledPins[0]) == HIGH ? 1u : 0u;
  for (uint8_t i = 0; i < N_LED; i++) {
    pinMode(ledPins[i], OUTPUT);
    digitalWrite(ledPins[i], HIGH);  // active-low: HIGH = off
  }

  // micros() resolution: smallest non-zero step between consecutive reads
  uint32_t best = 0xFFFFFFFFu, prev = micros();
  for (uint32_t i = 0; i < 20000; i++) {
    uint32_t m = micros();
    if (m != prev) { uint32_t d = m - prev; if (d < best) best = d; prev = m; }
  }
  microsRes = best;

  bridgeOk = Bridge.begin();
  if (bridgeOk) {
    Bridge.provide_safe("p_hb", onHb);
    Bridge.provide_safe("p_u8", onU8);
    Bridge.provide_safe("p_u16", onU16);
    Bridge.provide_safe("p_u32", onU32);
    Bridge.provide_safe("p_bool", onBool);
    Bridge.provide_safe("p_f", onFloat);
  } else {
    digitalWrite(ledPins[0], LOW);   // both reds on = Bridge.begin() failed
    digitalWrite(ledPins[3], LOW);
  }

  matrix.begin();
  matrix.setGrayscaleBits(3);
  buildFrame(0);
  uint32_t t0 = micros();
  for (uint8_t i = 0; i < 50; i++) matrix.draw(frame);
  matrixDrawX10 = (micros() - t0) / 5;  // average us x10
}

static void sendInfo(uint8_t key, uint32_t value) { Bridge.notify("p_info", key, value); }

static void loop() {
  inLoop = true;
  uint32_t nowUs = micros();
  uint32_t now = millis();
  if (havePrev) recordPass(nowUs - prevUs);
  prevUs = nowUs; havePrev = true;

  if (!bridgeOk) { inLoop = false; return; }

  // snapshot handler globals
  noInterrupts();
  bool fresh = rxFresh; uint32_t seq = rxSeq; rxFresh = false;
  bool ef[N_ECHO]; uint32_t ev[N_ECHO];
  for (uint8_t i = 0; i < N_ECHO; i++) { ef[i] = echoFlag[i]; ev[i] = echoValue[i]; echoFlag[i] = false; }
  interrupts();
  if (fresh) Bridge.notify("p_ack", seq);
  for (uint8_t i = 0; i < N_ECHO; i++) if (ef[i]) Bridge.notify("p_echo", i, ev[i]);

  // LED cycle: LED3 R,G,B then LED4 R,G,B then all off; 600 ms each
  static int8_t lastStep = -1;
  int8_t step = (int8_t)((now / 600u) % 7u);
  if (step != lastStep) {
    for (uint8_t i = 0; i < N_LED; i++) digitalWrite(ledPins[i], (int8_t)i == step ? LOW : HIGH);
    lastStep = step;
  }

  // floating A0 noise during the first second after setup
  if (now - startMs < 1000u) {
    int raw = analogRead(A0);
    if (raw < 0) adcFaults++;
    else { if (raw < adcMin) adcMin = raw; if (raw > adcMax) adcMax = raw; }
  }

  // matrix animation every 100 ms
  if (now - lastFrameMs >= 100u) {
    lastFrameMs = now;
    buildFrame(++dotPos);
    drawTimed();
    uint16_t c[16];
    for (uint8_t i = 0; i < 16; i++) c[i] = (uint16_t)(i * 64u + (dotPos % 64u));
    Bridge.notify("p_sample", now, c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7],
                  c[8], c[9], c[10], c[11], c[12], c[13], c[14], c[15]);
  }

  // ~2 KB notify burst once, measuring this pass
  if (!burstDone && now - startMs > 20000u) {
    burstDone = true;
    uint32_t t0 = micros();
    for (uint8_t k = 0; k < 40; k++)
      Bridge.notify("p_burst", (uint32_t)k, (uint16_t)1, (uint16_t)2, (uint16_t)3, (uint16_t)4,
                    (uint16_t)5, (uint16_t)6, (uint16_t)7, (uint16_t)8, (uint16_t)9, (uint16_t)10,
                    (uint16_t)11, (uint16_t)12, (uint16_t)13, (uint16_t)14, (uint16_t)15, (uint16_t)16);
    burstPassUs = micros() - t0;
  }

  // Serial (= Monitor on UNO Q) cost, once
  if (!serialDone && now - startMs > 25000u) {
    serialDone = true;
    uint32_t t0 = micros();
    Serial.begin(115200);
    serialBeginUs = micros() - t0;
    t0 = micros();
    Serial.println("iotc-probe: serial test line");
    serialPrintUs = micros() - t0;
  }

  // report every 2 s
  static uint32_t lastReport = 0;
  if (now - lastReport >= 2000u) {
    lastReport = now;
    uint32_t mean = passCount ? (uint32_t)(passSum / passCount) : 0;
    noInterrupts();
    uint32_t t = hbTotal, m = hbMainStack, o = hbOtherStack, d = hbDuringLoop;
    uint32_t dmin = hbDiffMin, dmax = hbDiffMax, age = now - rxBeatMs;
    interrupts();
    Bridge.notify("p_loop", now, passCount, passMin, histMedian(), passMax, mean, t, m, o, d);
    resetPassStats();
    sendInfo(1, microsRes);
    sendInfo(2, matrixDrawX10);
    sendInfo(3, (uint32_t)(adcMin < 0 ? 0 : adcMin));
    sendInfo(4, (uint32_t)(adcMax < 0 ? 0 : adcMax));
    sendInfo(5, pinmodeKeepsLevel);
    sendInfo(6, bridgeOk ? 1u : 0u);
    sendInfo(7, burstPassUs);
    sendInfo(8, serialPrintUs);
    sendInfo(9, serialBeginUs);
    sendInfo(10, dmin);
    sendInfo(11, dmax);
    sendInfo(12, matrixDrawMax);
    sendInfo(13, (uint32_t)adcFaults);
    sendInfo(14, age);
  }
  inLoop = false;
}

}  // namespace probe
