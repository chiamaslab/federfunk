#include "RefreshInterval.h"
#include <cassert>
#include <cstdio>
#include <string>

namespace {
constexpr uint32_t fallbackMs = 30UL * 60UL * 1000UL;
constexpr uint64_t fallbackUs = 1800000000ULL;

refresh::Response response(int status, bool conditional = false,
                           uint32_t hold = 0) {
  return refresh::classifyResponse(status, conditional, hold);
}
}  // namespace

int main() {
  using refreshInterval::parseSeconds;
  assert(parseSeconds(nullptr) == 0);
  for (const char* value : {"", "0", "299", "301", "3599", "43201", "43260",
                            "-3600", "+3600", " 3600", "3600 ", "3600.0",
                            "3.6e3", "3600,7200", "3600\r\n", "3600s",
                            "4294967296", "999999999999999999999"}) {
    assert(parseSeconds(value) == 0);
  }
  assert(parseSeconds(std::string(1000, '9').c_str()) == 0);
  assert(parseSeconds("0003600") == 3600);
  for (uint32_t minutes = 5; minutes <= 720; ++minutes) {
    assert(parseSeconds(std::to_string(minutes * 60).c_str()) == minutes * 60);
  }

  refreshInterval::Selection selection;
  assert(selection.seconds() == 0);
  assert(selection.sleepMicroseconds(fallbackMs, 0) == fallbackUs);

  // A new or identical validated 200 response can select a longer sleep, but
  // only after the entire image/battery-overlay/display operation succeeds.
  selection.stageResponse(response(200), "3600");
  assert(selection.seconds() == 0);
  assert(selection.sleepMicroseconds(fallbackMs, 0) == fallbackUs);
  selection.complete(true);
  assert(selection.seconds() == 3600);
  assert(selection.sleepMicroseconds(fallbackMs, 0) == 3600000000ULL);

  // Truncated content, bad colors/JPEG, cache-write or panel failures all
  // reach the same unsuccessful completion; even a battery fallback doesn't
  // change the failed network attempt into an accepted interval.
  selection.stageResponse(response(200), "43200");
  selection.complete(false);
  assert(selection.seconds() == 0);
  assert(selection.sleepMicroseconds(fallbackMs, 0) == fallbackUs);

  // Legitimate 304 + unchanged overlay, or successful cached badge update.
  selection.stageResponse(response(304, true), "7200");
  selection.complete(true);
  assert(selection.seconds() == 7200);
  selection.stageResponse(response(304, true), "300");
  selection.complete(false);  // Failed cached panel refresh.
  assert(selection.seconds() == 0);

  // An unsolicited 304 and all errors must reject interval proposals, even
  // if a caller accidentally reports an otherwise successful operation.
  for (int status : {-1, 204, 301, 302, 304, 400, 404, 500, 503}) {
    selection.stageResponse(response(status), "43200");
    selection.complete(true);
    assert(selection.seconds() == 0);
  }

  // A 304 whose cache is missing requires a new unconditional response.
  // Never carry its proposed interval through a retry that lacks a valid one.
  for (const char* replacement : {"", "3601", "43201"}) {
    selection.stageResponse(response(304, true), "7200");
    selection.beginResponse();
    selection.stageResponse(response(200), replacement);
    selection.complete(true);
    assert(selection.seconds() == 0);
  }
  selection.stageResponse(response(304, true), "7200");
  selection.beginResponse();
  selection.complete(false);  // Retry fails before receiving HTTP headers.
  assert(selection.seconds() == 0);
  selection.stageResponse(response(304, true), "7200");
  selection.stageResponse(response(200), "3600");
  selection.complete(true);
  assert(selection.seconds() == 3600);  // Only the final response wins.

  // Overnight deferrals still skip image/badge processing and override the
  // daytime interval, including a sub-minute hold immediately before sunrise.
  assert(selection.sleepMicroseconds(fallbackMs, 1) == 1000000ULL);
  assert(selection.sleepMicroseconds(fallbackMs, 86400) == 86400000000ULL);
  for (bool conditional : {false, true}) {
    for (int status : {200, 304}) {
      selection.stageResponse(response(status, conditional, 123), "43200");
      selection.complete(true);
      assert(selection.seconds() == 0);
      assert(selection.sleepMicroseconds(fallbackMs, 123) == 123000000ULL);
    }
  }

  selection.stageResponse(response(200), "43200");
  selection.complete(true);
  assert(selection.sleepMicroseconds(fallbackMs, 0) == 43200000000ULL);
  assert(selection.sleepMicroseconds(fallbackMs, UINT32_MAX) == 86400000000ULL);
  selection.beginResponse();
  assert(selection.sleepMicroseconds(UINT32_MAX, 0) == 4294967295000ULL);

  // The next wake starts from ordinary RAM, not the previous request or RTC.
  const refreshInterval::Selection nextWake;
  assert(nextWake.seconds() == 0);
  assert(nextWake.sleepMicroseconds(fallbackMs, 0) == fallbackUs);
  puts("PASS: bounded minute intervals, validated completion, retry replacement, "
       "overnight precedence, per-wake fallback and 64-bit sleep.");
}
