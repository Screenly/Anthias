// QtTest unit tests for the black-flash fallback predicate
// (src/image_fallback.cpp). Pure boolean logic, so this runs against
// QtCore alone: no display, no QtWebEngine. Hosted by the
// runImageFallbackTests factory that test_videoview.cpp's main()
// execs (QTEST_MAIN can only host one class per binary).
//
// Scope: this pins only the extracted predicate, given
// (currentImageIsNull, fallbackAllowed), which image paintEvent()
// should use. It does NOT pin which code paths set or clear
// fallbackAllowed (the "null" sentinel, playVideo(), loadPage(), and
// the QNetworkReply finished handler in View::loadImage()), that
// lifecycle still lives in view.cpp, which this test binary doesn't
// link (tests.pro excludes it to stay QtWebEngine-free). A regression
// in which paths set/clear the flag would not be caught here.

#include <QObject>
#include <QTest>

#include "image_fallback.h"

class TestImageFallback : public QObject
{
    Q_OBJECT

private slots:
    // Nothing to fall back to when there's already a real image on
    // screen: currentImage isn't null, so the flag doesn't matter.
    void currentImageNotNullNeverFallsBack_data()
    {
        QTest::addColumn<bool>("fallbackAllowed");
        QTest::newRow("allowed") << true;
        QTest::newRow("not-allowed") << false;
    }
    void currentImageNotNullNeverFallsBack()
    {
        QFETCH(bool, fallbackAllowed);
        QVERIFY(!image_fallback::shouldUseLastRasterImage(
            /*currentImageIsNull=*/false, fallbackAllowed));
    }

    // The three intentional blanks (playVideo(), the "null" sentinel,
    // loadPage()) all leave the flag false: must paint pure black,
    // never the stale frame.
    void intentionalBlankStaysBlack()
    {
        QVERIFY(!image_fallback::shouldUseLastRasterImage(
            /*currentImageIsNull=*/true, /*fallbackAllowed=*/false));
    }

    // A real fetch in flight from a blanked state (post-video) shows
    // the last real frame instead of black - the fix this PR adds.
    void inFlightFetchFromBlankUsesFallback()
    {
        QVERIFY(image_fallback::shouldUseLastRasterImage(
            /*currentImageIsNull=*/true, /*fallbackAllowed=*/true));
    }
};

int runImageFallbackTests(int argc, char** argv)
{
    TestImageFallback tc;
    return QTest::qExec(&tc, argc, argv);
}

#include "test_image_fallback.moc"
