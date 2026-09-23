#pragma once

#include <QByteArray>
#include <QtGlobal>

// Pure helpers behind the image-to-image crossfade transition
// (issue #3351). Extracted from View so the env-var parsing and the
// progress math are unit-testable against QtCore alone, the same
// shape as rotation.cpp/image_fallback.cpp (see tests/tests.pro).
//
// View owns all the QImage/QPainter/QTimer state (fadeFromImage,
// fadeActive, fadeElapsed, fadeTimer). This only answers pure
// questions asked of that state. Scope is image -> image only: video
// and web-page assets are untouched (see loadPage() / playVideo(),
// which blank currentImage before this logic ever sees it, so
// shouldStartFade's hasOutgoingImage is false whenever the previous
// asset was a video or a page).
namespace image_transition
{
// Default fade duration, in milliseconds, when
// ANTHIAS_IMAGE_TRANSITION_MS is unset or unparseable.
constexpr int kDefaultDurationMs = 300;

// A duration at or below this disables the transition outright:
// shouldStartFade() never arms one, so loadImage()/setupAnimation()
// fall back to today's instant swap. Also the floor for clamping, so
// a negative env value can't do anything stranger than ""disabled".
constexpr int kMinDurationMs = 0;

// Upper bound so a garbage/huge env value can't leave two assets
// cross-fading for the length of (or longer than) an asset's whole
// display slot. Mirrors the clamping style pageLoadTimeoutMs() (in
// view.cpp) already uses for ANTHIAS_WEBPAGE_TIMEOUT_S.
constexpr int kMaxDurationMs = 5000;

// Resolves ANTHIAS_IMAGE_TRANSITION_MS. "value" is qgetenv()'s
// result (empty when unset); ""set"" mirrors
// qEnvironmentVariableIsSet() so "unset" and "set to something that
// doesn't parse as an int" are distinguishable, both fall back to
// kDefaultDurationMs, then the result (default or parsed) is clamped
// to [kMinDurationMs, kMaxDurationMs].
int durationMs(const QByteArray &value, bool set);

// Whether the caller (View::loadAsStaticImage / View::setupAnimation)
// should arm a fade for the image that's about to be shown.
//
// hasOutgoingImage: there is a real, previously-decoded raster frame
// on screen to fade out of. Callers pass
// ``!currentImage.isNull()`` measured *before* overwriting it:false
// for the very first image after boot, and false whenever the prior
// asset was a video or web page (both blank currentImage before
// handing back to loadImage(), see loadPage()/playVideo() ), so
// neither case needs special-casing here.
//
// isNewAsset: true only when this call represents an actual
// asset-rotation into loadImage()/setupAnimation() for a *new* URI.
// A playing GIF's own QMovie::frameChanged repaint reuses the same
// currentImage member on every frame; that call site must pass
// false (and in practice never calls this function at all, you see
// setupAnimation()), or a playing GIF would appear to perpetually
// fade into itself.
//
// durationMs: the resolved value from durationMs() above.
bool shouldStartFade(
    bool hasOutgoingImage, bool isNewAsset, int durationMs);

// Fade progress in [0.0, 1.0] for a transition of ``durationMs`` ms
// that started ``elapsedMs`` ms ago. Monotonic and clamped at both
// ends. ``durationMs <= 0`` returns 1.0 (transition already
// complete) rather than dividing by zero, so callers are expected to
// have already filtered that case via shouldStartFade(), but this
// stays total so it's safe to call regardless.
qreal progressFor(int elapsedMs, int durationMs);
}  // namespace image_transition
