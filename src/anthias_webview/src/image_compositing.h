#pragma once

#include <QImage>
#include <QPainter>
#include <QRect>
#include <QSize>
#include <QtGlobal>

// Pure-ish compositing geometry/paint helpers behind the image
// crossfade (see image_transition.h for the progress/duration math).
// Split out from View so the actual pixel blend, not just the 0..1
// progress number, is unit-testable with a plain QImage, without
// pulling in QtWebEngine or even QWidget.
//
// This split exists because of a real bug caught on hardware, not as
// a speculative refactor: an earlier version composited the two
// frames with CompositionMode_Plus, which looked correct by the
// arithmetic (A*(1-p) + B*p sums to 1 for every p) but wasn't, because
// Qt's raster engine applies opacity to Plus by saturating the add
// first and interpolating the *result* against the original
// destination afterwards, not by scaling the source down before
// adding it. The pure progress-math tests (image_transition.cpp)
// stayed green throughout, because they never touch a pixel; only a
// test that actually composites and reads a colour back can catch
// this class of bug. See test_image_compositing.cpp.
namespace image_compositing
{
// The rect ``imageSize`` lands in when scaled (Qt::KeepAspectRatio)
// into ``box`` and centred. ``originAtCentre`` selects which of
// View::paintEvent()'s two centring conventions to use: false for a
// box anchored at (0,0) (the un-rotated path), true for a box
// centred at the painter's current origin (the linuxfb-rotation
// path, used after View has already translated+rotated the
// painter). Exposed rather than kept private to drawScaledImage() /
// paintCrossfade() so a caller can compute it once and reuse it.
// paintCrossfade() needs the incoming image's rect both to draw it
// and, separately, to know what it will NOT cover.
QRect scaledImageRect(
    const QSize &imageSize, const QSize &box, bool originAtCentre);

// Draws ``image`` into scaledImageRect(image.size(), box,
// originAtCentre), at ``opacity``. A no-op for a null image or an
// opacity of 0 or less. Painter opacity is left exactly as it was
// found whenever ``opacity`` is 1.0 or more (no setOpacity() call at
// all), so a non-fading, steady-state call doesn't perturb painter
// state that never needed touching.
void drawScaledImage(
    QPainter &painter, const QImage &image, const QSize &box,
    bool originAtCentre, qreal opacity);

// Composites one frame of a crossfade: ``outgoing`` fading out from
// under ``incoming`` fading in, at transition progress ``progress``
// in [0.0, 1.0], into ``box`` (same rotation/centring convention as
// scaledImageRect()). Draws, in order:
//
//   1. ``outgoing`` at full opacity, as the base layer, not
//      ``1 - progress``. This is the key difference from the
//      CompositionMode_Plus attempt: a single opaque source-over
//      draw at full opacity is exact on its own, nothing to get
//      wrong by combining it with anything else yet.
//   2. Black, at opacity ``progress``, over whatever part of
//      ``box`` ``incoming`` will NOT cover: its
//      Qt::KeepAspectRatio letterbox/pillarbox bars, on the (fairly
//      common in signage) occasions the two assets don't share an
//      aspect ratio. Without this, those bars would sit at
//      ``outgoing``'s full brightness for the entire fade and then
//      cut to black the instant it completes, which is exactly the
//      "pop" a crossfade exists to avoid, just moved to the edges
//      of the frame instead of the middle. Painted only over the
//      gap, never the area ``incoming`` itself covers (that's about
//      to be drawn over anyway, and this PR already has a
//      performance problem on low-end boards (see #3398) so
//      nothing here repaints a pixel that doesn't need it).
//   3. ``incoming`` at opacity ``progress``, source-over, on top.
//
// Steps 1 and 3 together, restricted to the region ``incoming``
// covers, equal ``(1 - progress) * outgoing + progress * incoming``
// exactly, for every value of ``progress``; plain source-over
// opacity blending over an opaque base layer, deliberately with NO
// composition-mode switch (see the namespace comment for why that
// matters here specifically). In the letterbox region, step 2 fades
// ``outgoing`` down to black in step with ``progress`` instead of
// holding it at full brightness, since there is nothing for that
// region to fade *to* other than black.
//
// The caller is responsible for everything outside the blend itself:
// filling ``box`` with an opaque background before calling this (so
// step 1's "full opacity" has a correct opaque layer under it, and
// so any sliver ``outgoing`` itself doesn't cover, if its own
// aspect ratio doesn't match ``box`` either, reads as that
// background rather than undefined); and the painter's transform
// (translate/rotate already applied by the caller when
// ``originAtCentre`` is true).
void paintCrossfade(
    QPainter &painter, const QImage &outgoing, const QImage &incoming,
    const QSize &box, bool originAtCentre, qreal progress);
}  // namespace image_compositing
