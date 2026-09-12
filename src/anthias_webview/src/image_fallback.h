#pragma once

// Pure predicate behind View::paintEvent()'s black-flash fallback (a
// real loadImage() fetch that starts from a blanked currentImage —
// typically right after a video asset: shows the last decoded raster
// frame instead of solid black while the fetch is in flight). The
// three intentional blanks (playVideo(), the ``"null"`` sentinel in
// loadImage(), loadPage()) must stay pure black, which is why this is
// gated by a flag rather than firing on every null currentImage.
//
// Kept free of View / QtWebEngine, the same way rotation.cpp is, so
// this one piece of the fallback logic is unit-testable against
// QtCore alone (see tests/tests.pro). View owns the QImage state
// (currentImage, lastRasterImage) and the flag itself; this only
// answers the yes/no question.
namespace image_fallback
{
// currentImageIsNull: View::currentImage.isNull() at paint time.
// fallbackAllowed: View::fallbackToLastImageOnBlank, true only while
// a real (non-"null") loadImage() request is outstanding; cleared on
// every terminal outcome of that request (success, network error, or
// decode failure) so a failed fetch goes black rather than leaving a
// stale asset on screen indistinguishable from a successful rotation.
//
// Returns true when paintEvent() should paint lastRasterImage in
// place of currentImage.
bool shouldUseLastRasterImage(bool currentImageIsNull, bool fallbackAllowed);
}  // namespace image_fallback
