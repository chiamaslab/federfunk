# Dashboard refresh interval

The firmware supports a daytime sleep interval supplied by the companion
dashboard. Device capability telemetry distinguishes compatible firmware from
older builds that retain their compiled interval.

## Protocol

Capable firmware adds `X-FederFunk-Refresh-Control: 1` to its existing image
GET. There is no extra request. A successful image response may include
`X-FederFunk-Refresh-Seconds: 3600` to select a one-hour sleep after this update.
The header applies to both HTTP 200 and a legitimate conditional HTTP 304.

The accepted interval is 300–43200 seconds (5 minutes–12 hours), in whole
minutes. Parsing permits decimal digits only, rejects out-of-range values,
and requires a multiple of 60. Missing or invalid values use the compiled
`config::refreshIntervalMs`, normally 30 minutes.

A 200 header is only a proposal until the image's length, content, cache,
pixel fingerprint and any necessary physical refresh succeed. Validated
identical pixels also count as success. For a 304, the retained image must be
legitimate and any necessary cached battery-badge update must succeed.
An unsolicited 304, failed download, invalid image, failed filesystem work or
failed panel refresh uses the bounded failure backoff described in
[the power policy](power-policy.md): normally 30, 60, then 120 minutes.
A successful battery
badge recovery after a network failure does not adopt a server interval.

If a 304 requires an unconditional retry because its cached source is missing
or corrupt, only the final response can propose an interval. Its missing or
invalid header cannot inherit the first response's value. The selection lives
in ordinary RAM for one wake; it is not persisted in RTC or flash.

## Overnight pause and timing

`X-FederFunk-Hold-Seconds` retains priority and its existing meaning: preserve
the entire image and battery badge, skip image processing and sleep until the
pause ends. Holds still accept a short interval near sunrise and are capped at
24 hours. A hold is not evidence that any new image was displayed.

The selected daytime value is a sleep duration after the update attempt,
matching the existing firmware. Download, refresh and shutdown time are
additional; it is not an exact wall-clock refresh cadence. A settings change
cannot reach an already sleeping device until its next wake. The overnight
schedule remains independent and takes effect when the device checks in.

The companion continues polling BirdNET and saving daily history independently
of device wake frequency. Longer intervals produce fewer battery samples;
dashboard staleness and forecast-gap rules must account for the selected
interval. The passive recorder still reads cached server state and never
wakes the frame.

## Rollout and verification

Deploy a compatible companion and firmware build, using the supported
hardware setup procedure and owner-authorized upload. The project default is
30 minutes. The capability header identifies support at the next ordinary
request. Older firmware ignores the response header and retains its compiled
interval, so the dashboard distinguishes a saved preference from confirmed
firmware support.

Local checks cover valid bounds, malformed/overflow values, success/failure
gating, conditional responses, retries, overnight precedence, per-wake reset
and 64-bit timer arithmetic. Both PlatformIO profiles must build after
shared firmware changes. For a hardware installation, confirm the chosen
sleep in a boot log and the next normal check-in, then check the failure
fallback and overnight resume.
No power-saving or elapsed-runtime claim follows from host tests alone.
