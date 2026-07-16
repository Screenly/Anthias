#pragma once

#include <QString>

// Manual content-rotation helpers for the Qt5 linuxfb boards (Pi 1/2/3).
// The linuxfb QPA plugin parses but silently ignores the ``rotation=N``
// option in ``QT_QPA_PLATFORM=linuxfb:...,rotation=N`` — the surface
// stays landscape — so images (View::paintEvent) and web pages (a
// stylesheet injected into QtWebEngine) have to be turned by hand to
// match the physically-rotated screen, the way the GStreamer video path
// already rotates via ``videoflip``. eglfs / wayland rotate the whole
// compositor output, so these return 0 / are unused there (no
// double-rotation).
//
// Kept free of View / QtWebEngine so the parsing and CSS-generation
// logic is unit-testable against QtCore alone (see tests/tests.pro).
namespace rotation
{
// Parse the manual rotation angle from QT_QPA_PLATFORM. Returns one of
// 0 / 90 / 180 / 270: 0 unless the platform is ``linuxfb`` and carries a
// ``rotation=N`` option that normalises (mod 360) to 90, 180 or 270.
int linuxfbRotationOverride();

// JavaScript that rotates a web page's document root to ``angle`` degrees
// clockwise with an !important stylesheet, re-asserted from a subtree
// MutationObserver so a page that removes the injected node can't drop
// it. 90/270 swap the viewport box so a landscape page fills the
// portrait-turned panel (pillar-boxed); 180 keeps the box.
QString webpageRotationScript(int angle);
}  // namespace rotation
