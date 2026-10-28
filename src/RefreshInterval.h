#pragma once

#include "RefreshState.h"
#include "ScheduleHold.h"
#include <stdint.h>

namespace refreshInterval {
constexpr char responseHeader[] = "X-FederFunk-Refresh-Seconds";
constexpr char capabilityHeader[] = "X-FederFunk-Refresh-Control";
constexpr char capabilityVersion[] = "1";
constexpr uint32_t minimumSeconds = 300;
constexpr uint32_t maximumSeconds = 43200;

// A missing/invalid interval means the compiled retry interval. Reject, rather
// than clamp, values outside whole minutes from five minutes through 12 hours.
inline uint32_t parseSeconds(const char* value) {
  if (value == nullptr || *value == '\0') return 0;
  uint32_t seconds = 0;
  for (const char* next = value; *next != '\0'; ++next) {
    if (*next < '0' || *next > '9') return 0;
    const uint32_t digit = static_cast<uint32_t>(*next - '0');
    if (seconds > (maximumSeconds - digit) / 10) return 0;
    seconds = seconds * 10 + digit;
  }
  return seconds >= minimumSeconds && seconds % 60 == 0 ? seconds : 0;
}

// Lives in ordinary RAM for one wake only. An HTTP response proposes a sleep;
// successful validation/display (or a valid unchanged image) must confirm it.
class Selection {
 public:
  void beginResponse() {
    candidateSeconds_ = 0;
    acceptedSeconds_ = 0;
  }

  void stageResponse(refresh::Response response, const char* header) {
    beginResponse();
    if (response == refresh::Response::Downloaded ||
        response == refresh::Response::NotModified) {
      candidateSeconds_ = parseSeconds(header);
    }
  }

  void complete(bool success) {
    acceptedSeconds_ = success ? candidateSeconds_ : 0;
    candidateSeconds_ = 0;
  }

  uint32_t seconds() const { return acceptedSeconds_; }

  uint64_t sleepMicroseconds(uint32_t defaultIntervalMs,
                            uint32_t holdSeconds) const {
    // A night hold may be shorter than five minutes near sunrise. Keep its
    // existing bounds and precedence independent of the daytime interval.
    return scheduleHold::sleepMicroseconds(
        defaultIntervalMs, holdSeconds > 0 ? holdSeconds : acceptedSeconds_);
  }

 private:
  uint32_t candidateSeconds_ = 0;
  uint32_t acceptedSeconds_ = 0;
};
}  // namespace refreshInterval
