#pragma once

#include <Arduino.h>
#include <assert.h>

// Test-only replacement for Arduino SPI. The application checks this marker
// so an include-path mistake cannot silently run the hardware transport.
#define BIRDDISPLAY_BITBANG_SPI_HEADER 1

#ifndef FSPI
#define FSPI 0
#endif
#ifndef SPI_MODE0
#define SPI_MODE0 0
#endif

class SPISettings {
 public:
  SPISettings(uint32_t clock, uint8_t bitOrder, uint8_t dataMode)
      : bitOrder(bitOrder), dataMode(dataMode) {
    // This test intentionally uses a fixed, slower clock than the driver asks.
    (void)clock;
  }
  const uint8_t bitOrder;
  const uint8_t dataMode;
};

class SPIClass {
 public:
  explicit SPIClass(uint8_t bus) { (void)bus; }

  void begin(int8_t sck, int8_t miso, int8_t mosi, int8_t ss) {
    // The display has no readback and its driver controls both CS pins itself.
    assert(sck >= 0 && mosi >= 0 && miso == -1 && ss == -1);
    sck_ = sck;
    mosi_ = mosi;
    pinMode(sck_, OUTPUT);
    pinMode(mosi_, OUTPUT);
    digitalWrite(sck_, LOW);
    digitalWrite(mosi_, LOW);
  }

  void beginTransaction(const SPISettings& settings) {
    assert(settings.bitOrder == MSBFIRST && settings.dataMode == SPI_MODE0);
    digitalWrite(sck_, LOW);
  }

  uint8_t transfer(uint8_t value) {
    // Match Waveshare's bit-banged mode-0, MSB-first waveform. Each half-clock
    // is at least 1 us (at most 500 kHz, plus digitalWrite overhead).
    for (uint8_t bit = 0; bit < 8; ++bit) {
      digitalWrite(mosi_, (value & 0x80U) != 0 ? HIGH : LOW);
      delayMicroseconds(1);
      digitalWrite(sck_, HIGH);
      delayMicroseconds(1);
      digitalWrite(sck_, LOW);
      value = static_cast<uint8_t>(value << 1);
    }
    return 0;  // No MISO/readback on this write-only display interface.
  }

  void writeBytes(const uint8_t* data, uint32_t length) {
    for (uint32_t index = 0; index < length; ++index) transfer(data[index]);
    // The unchanged display driver yields after each 300-byte row.
  }

  void endTransaction() { digitalWrite(sck_, LOW); }

  void end() {
    // GPIO ownership already belongs to this software transport. Match SPI's
    // lifecycle so the display driver can lower the pins before rail removal.
    if (sck_ >= 0) digitalWrite(sck_, LOW);
    if (mosi_ >= 0) digitalWrite(mosi_, LOW);
    sck_ = -1;
    mosi_ = -1;
  }

 private:
  int8_t sck_ = -1;
  int8_t mosi_ = -1;
};
