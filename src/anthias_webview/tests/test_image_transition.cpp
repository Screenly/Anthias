// QtTest unit tests for the image-to-image crossfade helpers
// (src/image_transition.cpp). Pure logic, so this runs against
// QtCore alone: no display, no QtWebEngine. Hosted by the
// runImageTransitionTests factory that test_videoview.cpp's main()
// execs (QTEST_MAIN can only host one class per binary).
//
// Scope: this pins durationMs()'s env-var parsing/clamping,
// shouldStartFade()'s arming predicate, and progressFor()'s
// monotonic clamp. It does NOT pin which call sites in view.cpp
// invoke these (loadAsStaticImage(), setupAnimation(), and the
// blanking paths that must stay fade-free).That lifecycle still
// lives in view.cpp, which this test binary doesn't link (tests.pro
// stays QtWebEngine-free).

#include <QObject>
#include <QTest>

#include "image_transition.h"

class TestImageTransition : public QObject
{
    Q_OBJECT

private slots:
    // --- durationMs() ---

    void unsetEnvVarUsesDefault()
    {
        QCOMPARE(
            image_transition::durationMs(QByteArray(), /*set=*/false),
            image_transition::kDefaultDurationMs);
    }

    void unparseableEnvVarUsesDefault()
    {
        QCOMPARE(
            image_transition::durationMs("abc", /*set=*/true),
            image_transition::kDefaultDurationMs);
    }

    void validEnvVarIsUsedVerbatim()
    {
        QCOMPARE(
            image_transition::durationMs("500", /*set=*/true), 500);
    }

    void envVarIsClampedToRange_data()
    {
        QTest::addColumn<QByteArray>("value");
        QTest::addColumn<int>("expected");
        QTest::newRow("negative-clamps-to-min")
            << QByteArray("-50") << image_transition::kMinDurationMs;
        QTest::newRow("zero-disables")
            << QByteArray("0") << 0;
        QTest::newRow("huge-clamps-to-max")
            << QByteArray("999999")
            << image_transition::kMaxDurationMs;
    }
    void envVarIsClampedToRange()
    {
        QFETCH(QByteArray, value);
        QFETCH(int, expected);
        QCOMPARE(
            image_transition::durationMs(value, /*set=*/true), expected);
    }

    // --- shouldStartFade() ---

    // First image after boot: nothing to fade from, must not arm.
    void noOutgoingImageNeverStartsFade()
    {
        QVERIFY(!image_transition::shouldStartFade(
            /*hasOutgoingImage=*/false, /*isNewAsset=*/true,
            /*durationMs=*/300));
    }

    // A GIF's own per-frame repaint must never (re)arm a fade, even
    // though there's a real outgoing image on screen: only an
    // actual asset change may.
    void gifFrameUpdateNeverStartsFade()
    {
        QVERIFY(!image_transition::shouldStartFade(
            /*hasOutgoingImage=*/true, /*isNewAsset=*/false,
            /*durationMs=*/300));
    }

    // ANTHIAS_IMAGE_TRANSITION_MS=0 (or clamped to it) must disable
    // the feature outright, not run a zero-length/instant "fade".
    void zeroDurationNeverStartsFade()
    {
        QVERIFY(!image_transition::shouldStartFade(
            /*hasOutgoingImage=*/true, /*isNewAsset=*/true,
            /*durationMs=*/0));
    }

    // The one case that should actually arm a fade.
    void newAssetWithOutgoingImageStartsFade()
    {
        QVERIFY(image_transition::shouldStartFade(
            /*hasOutgoingImage=*/true, /*isNewAsset=*/true,
            /*durationMs=*/300));
    }

    // --- progressFor() ---

    void progressClampsAtBothEnds()
    {
        QCOMPARE(image_transition::progressFor(-10, 300), qreal(0.0));
        QCOMPARE(image_transition::progressFor(0, 300), qreal(0.0));
        QCOMPARE(image_transition::progressFor(300, 300), qreal(1.0));
        QCOMPARE(image_transition::progressFor(9999, 300), qreal(1.0));
    }

    void progressIsLinearMidTransition()
    {
        QCOMPARE(image_transition::progressFor(150, 300), qreal(0.5));
    }

    // A disabled/zero duration must never divide by zero. Treated as
    // already-complete so a caller that (incorrectly) still calls
    // this doesn't get NaN/inf.
    void zeroDurationIsAlwaysComplete()
    {
        QCOMPARE(image_transition::progressFor(0, 0), qreal(1.0));
        QCOMPARE(image_transition::progressFor(50, 0), qreal(1.0));
    }
};

int runImageTransitionTests(int argc, char** argv)
{
    TestImageTransition tc;
    return QTest::qExec(&tc, argc, argv);
}

#include "test_image_transition.moc"
