# Offline 13.3E6 hardware smoke test

This separate application uses the current production display driver. It
does not read `BirdDisplayConfig.h`, connect to Wi-Fi, or access image servers.
The normal firmware and build configuration are unchanged.

From the repository root, after bootstrap:

```sh
.venv/bin/pio run -c platformio.hardware-test.ini
.venv/bin/pio run -c platformio.hardware-test.ini -t upload --upload-port YOUR_PORT
.venv/bin/pio device monitor --port YOUR_PORT --baud 115200 --dtr 0 --rts 0
```

Use the discovered native USB port. Before flashing, independently verify that
the attached board is the integrated ESP32-S3-ePaper-13.3E6 with **32 MB OPI
flash and 16 MB OPI PSRAM**. This build already contains a 32 MB partition
table, so its runtime memory check is not a substitute for identification
before upload. It shares the production toolchain cache at `.pio/core` and
keeps its build output in `.pio/hardware-test`.

Preserve the BS switch positions proven to work on this assembly. The owner
reported a successful six-color pattern after restoring the factory positions
(originally described as both OFF) and correcting reversed ribbon contacts.
Earlier local instructions to move both switches ON were superseded by that
result. Check contact orientation, seat the ribbon evenly and close its latch;
make any physical adjustments only with all power disconnected.

The test checks both reported memory sizes, then writes and reads two
complementary address-dependent patterns across the entire 960,000-byte PSRAM
framebuffer. It draws the same six-color pattern as
`scripts/generate_test_frame.py --panel large`, performs **one refresh**, and
powers down the display. Native USB stays awake for logs and the next upload.
Each reset runs the test again; it never refreshes from `loop()`.

Look for explicit `PASS` messages and the driver's `E-paper refresh complete.`
The driver must also report `refresh started (BUSY LOW)` before completion.
Allow up to two minutes for refresh. Confirm the physical image has a black
outline, a white margin, six vertical color bands, and red upper-left, blue
upper-right, and green lower-left corner markers. A serial pass cannot confirm
physical colors or orientation. A failed memory check stops before refresh.

This checks the allocated framebuffer, not every byte of PSRAM or flash.
It does not exercise networking, image decoding, or deep sleep. To install
the configured production firmware afterward:

```sh
.venv/bin/pio run -e esp32-s3-13in3e6 -t upload --upload-port YOUR_PORT
```

## Software SPI comparison

To isolate the Arduino hardware SPI transport, build the same test with:

```sh
.venv/bin/pio run -c platformio.hardware-test.ini -e esp32-s3-13in3e6-bitbang-test
.venv/bin/pio run -c platformio.hardware-test.ini -e esp32-s3-13in3e6-bitbang-test -t upload --upload-port YOUR_PORT
```

This environment substitutes `tests/hardware-bitbang/SPI.h` for Arduino's SPI
library. The same production driver still controls DC, both chip selects,
reset, panel power, register values, frame layout, and bounded BUSY checks.
Only the byte transport changes to GPIO writes on CLK 9 and MOSI 46, mode 0,
MSB first, with at least 1 microsecond per half-clock. The driver still yields
after every row. Frame transmission is slower than hardware SPI; allow up to
three minutes for the complete test.

The serial banner must say `Transport: software SPI`. A compile-time check
rejects a build that accidentally includes the hardware SPI header. Keep the
working switch positions described above; a schematic-based ON/OFF inference
did not establish the correct physical setting on this assembly. This
variant also performs just one refresh attempt per reset and keeps USB awake.

## Power settling comparison

The `esp32-s3-13in3e6-settle-test` environment uses hardware SPI and enables
the display rail 1000 ms before the driver's normal initialization/reset.
This isolates the longer settling interval used in other board examples,
without changing the production driver's timing. The same BUSY checks and
power cleanup apply; it makes one refresh attempt and leaves USB awake.

```sh
.venv/bin/pio run -c platformio.hardware-test.ini -e esp32-s3-13in3e6-settle-test -t upload --upload-port YOUR_PORT
```
