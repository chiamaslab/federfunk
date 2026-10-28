# Firmware power policy

Prepared on October 26, 2026. The implementation requires both firmware profile
builds and an authorized upload before it can affect a physical frame.

The normal update closes the final HTTP transfer, finishes any necessary cache
recovery, and turns Wi-Fi off before refreshing the panel. A conditional 304
with a damaged cache can still perform its unconditional recovery download.
Normal timer wakes skip the 1.5-second USB debugging delay; manual boots keep it.
Audio shutdown, panel power holds and the existing color pipeline are preserved.

## Update interval and failed requests

The existing interval capability supports dashboard-selected 30, 60, 120 minutes
and custom whole-minute intervals from 5 minutes to 12 hours. The compiled
default remains 30 minutes. Only a successfully validated/displayed image or
valid unchanged image adopts the response interval.

Consecutive failed wake cycles sleep for 30, 60, then 120 minutes, with 120
minutes as the continuing maximum. The first retry uses the compiled default.
A successful update or valid unchanged image resets the failure counter. Valid
overnight holds reset it and take priority over both retry backoff and battery
rechecks. Counters and prior wake results survive timer sleep, not manual resets.

## Low-voltage update pause

Only the integrated 13.3-inch board with a configured, installed battery applies
this policy. Before filesystem mounting, framebuffer allocation or networking,
it reads the ADC with the radio and panel supply off. A reading at or below the
cutoff is checked by a second independent trimmed sample. If both are low,
updates pause. Invalid samples for a confirmed battery also pause updates.
Charging status does not bypass the voltage check.

The default cutoff is **3.30 V**. Once paused, both samples must reach cutoff
plus **0.15 V** to resume. The firmware retains the pause across timer sleep and
checks only the battery every 30 minutes. During a pause it does not connect to
Wi-Fi, download an image or repaint even the low-battery badge. A newly raised
server cutoff is checked before committing/displaying a new image as well.
The current display state and last successful image revision remain intact.

This reduces update load. It is **not a hardware battery disconnect** and cannot
eliminate board standby draw or protect an unattended cell indefinitely.
The voltage is the board ADC estimate, not a precision cell measurement or a
measurement of remaining capacity.

## HTTP protocol and persistence

The integrated board advertises `X-FederFunk-Power-Control: 1` and sends its
currently configured `X-FederFunk-Stop-Millivolts` on the ordinary image GET.
The server may reply with `X-FederFunk-Stop-Millivolts` containing a decimal
integer from **3200 through 3600**, in **50 mV** increments. Missing, malformed
and out-of-range headers leave the existing cutoff unchanged.

A bounded cutoff is accepted on HTTP 200 or a legitimate conditional 304,
including a schedule hold. It is independent of image validation because a
broken image must not prevent a safer cutoff from being adopted. An unsolicited
304 cannot change the cutoff. The accepted value is written to the separate
NVS namespace `federfunk-pwr`, key `stop-mv`, only when it changes. An invalid
stored value falls back to 3300 mV. If NVS fails, the received cutoff still
applies to the current wake and a serial message reports the persistence error.

The normal GET also reports retained outcomes, without another connection:

| Request header | Meaning |
| --- | --- |
| `X-FederFunk-Last-Displayed-ETag` | Valid source ETag of the last successfully displayed or verified-identical frame; omitted when unknown |
| `X-FederFunk-Previous-Result` | `updated`, `unchanged`, `failed`, `scheduled_hold` or `low_battery` |
| `X-FederFunk-Previous-Sleep-Seconds` | Sleep chosen after that previous wake, including backoff/hold; it is not proof of elapsed time |

The last-displayed revision is confirmation of an earlier successful cycle,
never proof that the current GET was displayed. Cold boots omit retained
outcomes. A low-voltage pause sends **no request**, so the dashboard cannot
claim a newly confirmed pause without subsequent telemetry. When voltage
recovers, the next ordinary GET can report the retained `low_battery` result.
These outcome headers work on both profiles; battery-control capability is
advertised only by the supported installed-battery profile.

## Verification

`python3 -m unittest discover -s tests -p 'test_power_policy.py'` exercises strict
cutoff parsing, write-on-change decisions, low entry, invalid-reading behavior,
resume hysteresis, retry bounds/reset, schedule priority and retained outcome
validation under address/undefined-behavior sanitizers. Integration checks
verify guards precede filesystem/panel work and that final Wi-Fi shutdown
follows the optional cache-recovery download.

Hardware verification must confirm a full refresh, timer wake, selected sleep,
telemetry and recovery. Neither successful host tests nor a serial shutdown
message measure real sleep current.
