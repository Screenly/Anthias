#include "image_transition.h"

#include <algorithm>

namespace image_transition
{

int durationMs(const QByteArray &value, bool set)
{
    int parsed = kDefaultDurationMs;
    if (set) {
        bool ok = false;
        const int candidate = value.toInt(&ok);
        if (ok) {
            parsed = candidate;
        }
        // Set but unparseable (e.g."abc"): silently keep the
        // default, same posture pageLoadTimeoutMs() takes for
        // ANTHIAS_WEBPAGE_TIMEOUT_S: a malformed override shouldn't
        // crash or disable the feature,just fall back.
    }
    return std::clamp(parsed, kMinDurationMs, kMaxDurationMs);
}

bool shouldStartFade(
    bool hasOutgoingImage, bool isNewAsset, int durationMs)
{
    return hasOutgoingImage && isNewAsset && durationMs > kMinDurationMs;
}

qreal progressFor(int elapsedMs, int durationMs)
{
    if (durationMs <= 0) {
        return 1.0;
    }
    if (elapsedMs <= 0) {
        return 0.0;
    }
    if (elapsedMs >= durationMs) {
        return 1.0;
    }
    return static_cast<qreal>(elapsedMs) / static_cast<qreal>(durationMs);
}

}  // namespace image_transition
