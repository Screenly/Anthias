// QImage-backed tests for the actual crossfade *pixel* blend
// (src/image_compositing.cpp), as opposed to test_image_transition
// .cpp, which only pins the 0..1 progress arithmetic. Compiles
// against QtGui + QtCore + QtTest alone (QImage/QPainter/QRegion),
// no QWidget and no QtWebEngine, so it's hosted the same way as
// test_rotation.cpp / test_image_fallback.cpp (see tests/tests.pro):
// the runImageCompositingTests factory that test_videoview.cpp's
// main() execs, since QTEST_MAIN can only host one class per binary.
//
// This file exists because of a real bug an earlier version of
// paintCrossfade() had (composited with CompositionMode_Plus) that
// test_image_transition.cpp's tests all stayed green through, since
// progressFor()'s arithmetic was never wrong, only the pixels it
// fed into were. See image_compositing.h's namespace comment for the
// mechanism. Every expected colour below is chosen so the blend
// divides evenly (no rounding ambiguity), and the
// (220,180,120)->(90,160,230) pair is deliberately the exact example
// from the hardware review that found the bug, so these numbers are
// a direct regression pin against it, not just a plausible-looking
// new test.

#include <QColor>
#include <QImage>
#include <QObject>
#include <QPainter>
#include <QTest>

#include "image_compositing.h"

namespace
{
QImage solidImage(const QSize &size, const QColor &color)
{
    QImage image(size, QImage::Format_RGB32);
    image.fill(color);
    return image;
}

// Renders paintCrossfade() into a ``box``-sized, black-filled
// QImage, matching View::paintEvent()'s own
// painter.fillRect(rect(), Qt::black) immediately before any of this
// runs, and returns the result for pixel inspection.
QImage renderCrossfade(
    const QImage &outgoing, const QImage &incoming, const QSize &box,
    qreal progress)
{
    QImage canvas(box, QImage::Format_RGB32);
    canvas.fill(Qt::black);
    QPainter painter(&canvas);
    image_compositing::paintCrossfade(
        painter, outgoing, incoming, box, /*originAtCentre=*/false,
        progress);
    return canvas;
}
}  // namespace

class TestImageCompositing : public QObject
{
    Q_OBJECT

private slots:
    // Same-aspect-ratio pairs (no letterbox gap): the blend
    // should be exactly (1-p)*outgoing + p*incoming everywhere,
    // which is what CompositionMode_Plus got wrong. Mirrors the
    // exact pairs and progress point (p=0.5) from the hardware
    // review table.

    void midpointBlend_data()
    {
        QTest::addColumn<QColor>("outgoing");
        QTest::addColumn<QColor>("incoming");
        QTest::addColumn<QColor>("expected");

        // white -> white: under the Plus bug this read 190, not 255
        // (the single clearest case, since there's no colour
        // mixing to obscure the dip).
        QTest::newRow("white-to-white")
            << QColor(255, 255, 255) << QColor(255, 255, 255)
            << QColor(255, 255, 255);

        // grey(200) -> grey(200): Plus produced 177, not 200.
        QTest::newRow("grey200-to-grey200")
            << QColor(200, 200, 200) << QColor(200, 200, 200)
            << QColor(200, 200, 200);

        // The exact pair from the hardware review's worked example.
        QTest::newRow("hardware-review-example")
            << QColor(220, 180, 120) << QColor(90, 160, 230)
            << QColor(155, 170, 175);

        // dark(40) -> dark(60): the one pair in the review's table
        // where nothing saturates, so even the buggy Plus version
        // happened to land on the right answer (50) here. Included
        // so this test doesn't only cover the cases that used to
        // fail, it also covers the one that used to accidentally
        // pass, to make sure the new code doesn't regress it.
        QTest::newRow("dark40-to-dark60")
            << QColor(40, 40, 40) << QColor(60, 60, 60)
            << QColor(50, 50, 50);
    }

    void midpointBlend()
    {
        QFETCH(QColor, outgoing);
        QFETCH(QColor, incoming);
        QFETCH(QColor, expected);

        const QSize box(100, 100);
        const QImage result = renderCrossfade(
            solidImage(box, outgoing), solidImage(box, incoming), box,
            /*progress=*/0.5);

        QCOMPARE(result.pixelColor(50, 50), expected);
    }

    // p=0: should read as pure outgoing; the fade hasn't visibly
    // started (matches the "first image after boot" / "no fade-in
    // from black" requirement: at the instant a fade arms,
    // progress==0 and nothing should look different yet).
    void progressZeroIsPureOutgoing()
    {
        const QSize box(100, 100);
        const QImage result = renderCrossfade(
            solidImage(box, QColor(220, 180, 120)),
            solidImage(box, QColor(90, 160, 230)), box,
            /*progress=*/0.0);

        QCOMPARE(result.pixelColor(50, 50), QColor(220, 180, 120));
    }

    // p=1: should read as pure incoming; the fade has fully
    // resolved (matches paintEvent()'s own fadeProgress >= 1.0 ->
    // stopImageFade() cutover to the plain, non-blending draw).
    void progressOneIsPureIncoming()
    {
        const QSize box(100, 100);
        const QImage result = renderCrossfade(
            solidImage(box, QColor(220, 180, 120)),
            solidImage(box, QColor(90, 160, 230)), box,
            /*progress=*/1.0);

        QCOMPARE(result.pixelColor(50, 50), QColor(90, 160, 230));
    }

    // Letterbox gap: outgoing matches the box's aspect ratio
    // (400x300, fills it exactly), incoming is much wider than tall
    // (400x100), so Qt::KeepAspectRatio centres it with 100px bars
    // top and bottom. The covered centre strip should blend exactly
    // like the no-gap cases above; the bars, which the incoming
    // image never reaches, should fade from outgoing's colour
    // toward black in step with progress, not sit at full brightness
    // until the fade completes.

    void letterboxGapFadesToBlack()
    {
        const QSize box(400, 300);
        const QColor outgoingColor(220, 180, 120);
        const QColor incomingColor(90, 160, 230);
        const QImage outgoing = solidImage(box, outgoingColor);
        // 400x100: fits the box at scale 1 (min(400/400, 300/100) =
        // min(1, 3) = 1), landing as a 400x100 strip centred inside
        // the 400x300 box: a 100px bar above and below it.
        const QImage incoming = solidImage(QSize(400, 100), incomingColor);

        const QImage result =
            renderCrossfade(outgoing, incoming, box, /*progress=*/0.5);

        // Centre of the incoming strip (y in [100, 200)): the same
        // plain blend as the no-gap cases, unaffected by the bars
        // existing at all.
        QCOMPARE(result.pixelColor(200, 150), QColor(155, 170, 175));

        // Centre of the top bar (y in [0, 100), never reached by
        // incoming): outgoing faded to half brightness, not left at
        // full brightness and not black either; this is exactly
        // the "pop" fillLetterboxGaps() exists to avoid.
        QCOMPARE(result.pixelColor(200, 50), QColor(110, 90, 60));
        // Bottom bar (y in [200, 300)) is symmetric.
        QCOMPARE(result.pixelColor(200, 250), QColor(110, 90, 60));
    }

    void letterboxGapAtProgressZeroIsOutgoing()
    {
        const QSize box(400, 300);
        const QColor outgoingColor(220, 180, 120);
        const QImage outgoing = solidImage(box, outgoingColor);
        const QImage incoming =
            solidImage(QSize(400, 100), QColor(90, 160, 230));

        const QImage result =
            renderCrossfade(outgoing, incoming, box, /*progress=*/0.0);

        // Nothing has started yet: the whole box, bars included,
        // reads as outgoing.
        QCOMPARE(result.pixelColor(200, 50), outgoingColor);
        QCOMPARE(result.pixelColor(200, 150), outgoingColor);
    }

    void letterboxGapAtProgressOneIsBlackOrIncoming()
    {
        const QSize box(400, 300);
        const QColor incomingColor(90, 160, 230);
        const QImage outgoing = solidImage(box, QColor(220, 180, 120));
        const QImage incoming = solidImage(QSize(400, 100), incomingColor);

        const QImage result =
            renderCrossfade(outgoing, incoming, box, /*progress=*/1.0);

        // The bars have nothing to fade to but black, and the strip
        // reads as pure incoming: matching the non-blending path
        // View::paintEvent() switches to once a fade completes
        // (which paints the letterbox bars via the original
        // fillRect(rect(), Qt::black), not via this function at
        // all; This just confirms paintCrossfade() itself is
        // already consistent with that end state one frame early).
        QCOMPARE(result.pixelColor(200, 50), QColor(Qt::black));
        QCOMPARE(result.pixelColor(200, 150), incomingColor);
    }

    // A gap-filling draw must never touch the region the incoming
    // image itself covers: both for correctness (the incoming
    // image's own draw already lands the right colour there, and a
    // black overlay underneath wouldn't change the final result, but
    // doing it is still wasted work on hardware this PR already
    // knows is too slow), but one wrong index in the QRegion
    // subtraction could paint into or shrink the wrong zone, so this
    // pins the covered strip's untouched edge explicitly.
    void letterboxGapDoesNotEncroachOnCoveredRegion()
    {
        const QSize box(400, 300);
        const QImage outgoing = solidImage(box, QColor(220, 180, 120));
        const QImage incoming =
            solidImage(QSize(400, 100), QColor(90, 160, 230));

        const QImage result =
            renderCrossfade(outgoing, incoming, box, /*progress=*/0.5);

        // y=99 is the last bar row, y=100 is the first covered row
        // (incomingRect top is at (300-100)/2 = 100): both should
        // be on their expected side of the boundary, confirming the
        // gap region and the covered region meet exactly where
        // scaledImageRect() says they do.
        QCOMPARE(result.pixelColor(200, 99), QColor(110, 90, 60));
        QCOMPARE(result.pixelColor(200, 100), QColor(155, 170, 175));
    }

    // Matching aspect ratios (the common case for same-orientation
    // signage content) must produce no gap at all; fillLetterboxGaps
    // ends up a no-op via its QRegion::isEmpty() check, so this is
    // really pinning that the common case stays on the cheap path.
    void matchingAspectRatioHasNoGap()
    {
        const QSize box(400, 300);
        const QImage outgoing = solidImage(box, QColor(220, 180, 120));
        const QImage incoming = solidImage(box, QColor(90, 160, 230));

        const QImage result =
            renderCrossfade(outgoing, incoming, box, /*progress=*/0.5);

        // Every corner should show the plain blend, not a
        // partially-black bar: there is no bar to show.
        QCOMPARE(result.pixelColor(5, 5), QColor(155, 170, 175));
        QCOMPARE(result.pixelColor(394, 294), QColor(155, 170, 175));
    }

    // scaledImageRect() geometry, in isolation

    void scaledImageRectCentresAndPreservesAspectRatio()
    {
        // A 4:1 image into a 4:3 box, un-rotated convention: scale
        // is limited by height (box height 300 / image height 100 =
        // 3, vs width 400/400 = 1: KeepAspectRatio takes the
        // smaller), so it should land as the full 400x100 width,
        // centred vertically, flush with the box's left/top origin
        // horizontally (no horizontal letterboxing needed here).
        const QRect r = image_compositing::scaledImageRect(
            QSize(400, 100), QSize(400, 300), /*originAtCentre=*/false);
        QCOMPARE(r, QRect(0, 100, 400, 100));
    }

    void scaledImageRectOriginAtCentreIsCentredOnOrigin()
    {
        // Same scale as above, but the originAtCentre convention
        // used after View has already translated the painter to the
        // widget's centre: the rect should be centred on (0,0)
        // rather than anchored at the box's top-left.
        const QRect r = image_compositing::scaledImageRect(
            QSize(400, 100), QSize(400, 300), /*originAtCentre=*/true);
        QCOMPARE(r, QRect(-200, -50, 400, 100));
    }
};

int runImageCompositingTests(int argc, char** argv)
{
    TestImageCompositing tc;
    return QTest::qExec(&tc, argc, argv);
}

#include "test_image_compositing.moc"
