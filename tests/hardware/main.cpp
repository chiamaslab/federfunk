#include <Arduino.h>
#include <SPI.h>
#include <esp_heap_caps.h>
#include <esp_psram.h>

#include "../../src/Epaper13in3e.h"

#if defined(BIRDDISPLAY_SOFTWARE_SPI) && !defined(BIRDDISPLAY_BITBANG_SPI_HEADER)
#error "Software SPI test must select tests/hardware-bitbang/SPI.h"
#endif

namespace {

constexpr uint32_t expectedFlashBytes = 32U * 1024U * 1024U;
constexpr uint32_t expectedPsramBytes = 16U * 1024U * 1024U;
constexpr uint8_t colors[] = {0x0, 0x1, 0x2, 0x3, 0x5, 0x6};

SPIClass epdSpi(FSPI);
// Integrated board: SCK, MOSI, master CS, slave CS, DC, RST, BUSY, power.
Epaper13in3e panel(epdSpi, 9, 46, 10, 3, 11, 2, 12, 1);
uint8_t* frame = nullptr;

void finishTest(bool success) {
  panel.prepareForDeepSleep();
  heap_caps_free(frame);
  frame = nullptr;
  Serial.println(success ? "PASS: offline hardware smoke test completed."
                         : "FAIL: offline hardware smoke test stopped.");
  Serial.println("Display power is off; USB remains awake. No automatic refresh.");
  Serial.println("Reset repeats this test. Upload production firmware when ready.");
  Serial.flush();
}

uint8_t memoryPattern(size_t index, bool complement) {
  const uint32_t address = static_cast<uint32_t>(index);
  const uint8_t value = static_cast<uint8_t>(
      (address * 37U) ^ (address >> 8) ^ (address >> 16) ^ 0xA5U);
  return complement ? static_cast<uint8_t>(~value) : value;
}

bool checkFrameMemory() {
  // Volatile accesses force actual writes and reads across the entire buffer.
  volatile uint8_t* memory = frame;
  for (uint8_t pass = 0; pass < 2; ++pass) {
    for (size_t index = 0; index < Epaper13in3e::frameBytes; ++index) {
      memory[index] = memoryPattern(index, pass != 0);
      if ((index & 0x7FFFU) == 0) delay(1);
    }
    for (size_t index = 0; index < Epaper13in3e::frameBytes; ++index) {
      const uint8_t expected = memoryPattern(index, pass != 0);
      const uint8_t actual = memory[index];
      if (actual != expected) {
        Serial.printf("FAIL: PSRAM pass %u at byte %u: read %02X, expected %02X.\n",
                      static_cast<unsigned>(pass + 1),
                      static_cast<unsigned>(index),
                      static_cast<unsigned>(actual),
                      static_cast<unsigned>(expected));
        return false;
      }
      if ((index & 0x7FFFU) == 0) delay(1);
    }
    Serial.printf("PASS: PSRAM pattern %u wrote/read all %u framebuffer bytes.\n",
                  static_cast<unsigned>(pass + 1),
                  static_cast<unsigned>(Epaper13in3e::frameBytes));
  }
  return true;
}

uint8_t colorAt(uint16_t x, uint16_t y) {
  // Keep the geometry identical to scripts/generate_test_frame.py --panel large.
  constexpr uint16_t width = Epaper13in3e::width;
  constexpr uint16_t height = Epaper13in3e::height;
  constexpr uint16_t marker = width / 40;
  if (x < 2 || y < 2 || x >= width - 2 || y >= height - 2) return 0x0;
  if (x < marker || y < marker || x >= width - marker || y >= height - marker)
    return 0x1;
  if (x < marker * 3 && y < marker * 3) return 0x3;
  if (x >= width - marker * 3 && y < marker * 3) return 0x5;
  if (x < marker * 3 && y >= height - marker * 3) return 0x6;
  return colors[static_cast<uint32_t>(x) * 6U / width];
}

void makeTestFrame() {
  for (uint16_t y = 0; y < Epaper13in3e::height; ++y) {
    for (uint16_t x = 0; x < Epaper13in3e::width; x += 2) {
      const size_t offset = static_cast<size_t>(y) * (Epaper13in3e::width / 2) +
                            x / 2;
      frame[offset] = static_cast<uint8_t>((colorAt(x, y) << 4) | colorAt(x + 1, y));
    }
    if ((y & 31U) == 0) delay(1);
  }
}

}  // namespace

void setup() {
  // Hold panel power off while checking memory; do not load private settings.
  panel.begin();
  Serial.begin(115200);
  delay(3000);
  Serial.println("\nFederFunk offline 13.3E6 hardware smoke test");
#ifdef BIRDDISPLAY_SOFTWARE_SPI
  Serial.println("Transport: software SPI, CLK 9 / MOSI 46, 1 us minimum half-clock.");
#else
  Serial.println("Transport: Arduino hardware SPI.");
#endif
  Serial.println("This test performs one display refresh per reset, without Wi-Fi.");

  const uint32_t flashBytes = ESP.getFlashChipSize();
  // Arduino's getPsramSize reports usable heap, which excludes allocator
  // overhead. Compare the physical capacity to the module specification.
  const uint32_t psramBytes = psramFound() ? esp_psram_get_size() : 0;
  Serial.printf("Detected flash: %u bytes; PSRAM: %u bytes.\n",
                static_cast<unsigned>(flashBytes), static_cast<unsigned>(psramBytes));
  if (flashBytes != expectedFlashBytes || psramBytes != expectedPsramBytes) {
    Serial.println("FAIL: expected 33554432-byte flash and 16777216-byte PSRAM.");
    finishTest(false);
    return;
  }
  Serial.println("PASS: expected flash and PSRAM sizes detected.");
  Serial.printf("Usable PSRAM heap: %u bytes.\n", ESP.getPsramSize());

  frame = static_cast<uint8_t*>(heap_caps_malloc(
      Epaper13in3e::frameBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (frame == nullptr) {
    Serial.println("FAIL: could not allocate 960000-byte framebuffer in PSRAM.");
    finishTest(false);
    return;
  }
  if (!checkFrameMemory()) {
    finishTest(false);
    return;
  }

  makeTestFrame();
  Serial.println("Prepared 1600x1200 six-color test pattern (960000 bytes).");
  Serial.println("Expected: red upper-left, blue upper-right, green lower-left markers.");
  Serial.println("Starting the only refresh; allow up to two minutes.");
#ifdef BIRDDISPLAY_POWER_SETTLE_TEST
  // The production driver sets this same enable HIGH before resetting. Give
  // the rail a full second to settle first, keeping its normal cleanup path.
  Serial.println("Diagnostic: enabling display power 1000 ms before driver reset.");
  digitalWrite(1, HIGH);
  delay(1000);
  Serial.printf("BUSY after power settling: %s.\n",
                digitalRead(12) == HIGH ? "HIGH" : "LOW");
#endif
  const bool refreshed = panel.display(frame);
  Serial.println(refreshed ? "PASS: driver completed the display refresh."
                           : "FAIL: driver could not complete the display refresh.");
  if (refreshed) Serial.println("Visual confirmation of colors/orientation is still required.");
  finishTest(refreshed);
}

void loop() {
  // Keep native USB alive for logs and the next upload. Never refresh here.
  delay(1000);
}
