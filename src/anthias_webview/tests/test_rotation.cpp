// QtTest unit tests for the linuxfb manual-rotation helpers
// (src/rotation.cpp), the Pi 1/2/3 path where the Qt5 linuxfb QPA
// plugin ignores ``rotation=N`` so images and web pages have to be
// turned by hand. The helpers are pure (QT_QPA_PLATFORM parsing and
// CSS string generation), so these run against QtCore alone — no
// display, no QtWebEngine. Hosted by the runRotationTests factory that
// test_videoview.cpp's main() execs (QTEST_MAIN can only host one
// class per binary).

#include <QByteArray>
#include <QObject>
#include <QString>
#include <QTest>

#include "rotation.h"

namespace
{
// RAII guard: set QT_QPA_PLATFORM for one test and restore it after, so
// the cases don't leak platform strings into each other (or the runner).
// A null QByteArray means "unset entirely" (qunsetenv) rather than "set
// to empty string" — so the unset case genuinely exercises the
// no-variable path, not an empty-string one.
class QpaGuard
{
public:
    explicit QpaGuard(const QByteArray& value)
        : had_(qEnvironmentVariableIsSet("QT_QPA_PLATFORM"))
        , previous_(qgetenv("QT_QPA_PLATFORM"))
    {
        if (value.isNull()) {
            qunsetenv("QT_QPA_PLATFORM");
        } else {
            qputenv("QT_QPA_PLATFORM", value);
        }
    }
    ~QpaGuard()
    {
        if (had_) {
            qputenv("QT_QPA_PLATFORM", previous_);
        } else {
            qunsetenv("QT_QPA_PLATFORM");
        }
    }

private:
    bool had_;
    QByteArray previous_;
};
}  // namespace

class TestRotation : public QObject
{
    Q_OBJECT

private slots:
    // linuxfbRotationOverride() only fires on linuxfb; every other
    // platform (and an unset one) rotates at the compositor and must
    // return 0 so paintEvent doesn't double-rotate.
    void overrideNonLinuxfbIsZero_data()
    {
        QTest::addColumn<QByteArray>("qpa");
        QTest::newRow("unset") << QByteArray();
        QTest::newRow("eglfs") << QByteArray("eglfs");
        QTest::newRow("wayland") << QByteArray("wayland");
        QTest::newRow("offscreen") << QByteArray("offscreen");
        // A rotation option on a non-linuxfb platform is still ignored.
        QTest::newRow("eglfs-with-rotation")
            << QByteArray("eglfs:rotation=90");
    }
    void overrideNonLinuxfbIsZero()
    {
        QFETCH(QByteArray, qpa);
        QpaGuard guard(qpa);
        QCOMPARE(rotation::linuxfbRotationOverride(), 0);
    }

    // linuxfb without a valid rotation=N option is unrotated (0).
    void overrideLinuxfbNoAngleIsZero_data()
    {
        QTest::addColumn<QByteArray>("qpa");
        QTest::newRow("bare") << QByteArray("linuxfb");
        QTest::newRow("no-option") << QByteArray("linuxfb:");
        QTest::newRow("other-option")
            << QByteArray("linuxfb:fb=/dev/fb0");
        QTest::newRow("non-numeric")
            << QByteArray("linuxfb:rotation=left");
        QTest::newRow("unsupported-45")
            << QByteArray("linuxfb:rotation=45");
        QTest::newRow("360-normalises-to-0")
            << QByteArray("linuxfb:rotation=360");
    }
    void overrideLinuxfbNoAngleIsZero()
    {
        QFETCH(QByteArray, qpa);
        QpaGuard guard(qpa);
        QCOMPARE(rotation::linuxfbRotationOverride(), 0);
    }

    // Valid angles are parsed and normalised (mod 360, negatives wrap).
    void overrideLinuxfbAngle_data()
    {
        QTest::addColumn<QByteArray>("qpa");
        QTest::addColumn<int>("expected");
        QTest::newRow("90") << QByteArray("linuxfb:rotation=90") << 90;
        QTest::newRow("180") << QByteArray("linuxfb:rotation=180") << 180;
        QTest::newRow("270") << QByteArray("linuxfb:rotation=270") << 270;
        QTest::newRow("450-wraps-to-90")
            << QByteArray("linuxfb:rotation=450") << 90;
        QTest::newRow("-90-wraps-to-270")
            << QByteArray("linuxfb:rotation=-90") << 270;
        QTest::newRow("angle-among-options")
            << QByteArray("linuxfb:fb=/dev/fb0,rotation=180") << 180;
    }
    void overrideLinuxfbAngle()
    {
        QFETCH(QByteArray, qpa);
        QFETCH(int, expected);
        QpaGuard guard(qpa);
        QCOMPARE(rotation::linuxfbRotationOverride(), expected);
    }

    // The injected script carries the requested angle and always
    // re-asserts itself from a subtree MutationObserver.
    void webpageScriptCommonShape_data()
    {
        QTest::addColumn<int>("angle");
        QTest::newRow("90") << 90;
        QTest::newRow("180") << 180;
        QTest::newRow("270") << 270;
    }
    void webpageScriptCommonShape()
    {
        QFETCH(int, angle);
        const QString js = rotation::webpageRotationScript(angle);
        QVERIFY(js.contains(
            QStringLiteral("rotate(%1deg)").arg(angle)));
        QVERIFY(js.contains(QStringLiteral("anthias-rotation")));
        QVERIFY(js.contains(QStringLiteral("!important")));
        // Robustness against SPAs removing the node deep in the tree.
        QVERIFY(js.contains(QStringLiteral("subtree:true")));
        QVERIFY(js.contains(QStringLiteral("MutationObserver")));
    }

    // 90/270 swap the viewport box (portrait fill, pillar-boxed); 180
    // keeps the landscape box. The box governs whether a landscape page
    // fits the portrait-turned panel.
    void webpageScript90SwapsBox()
    {
        const QString js = rotation::webpageRotationScript(90);
        QVERIFY(js.contains(QStringLiteral("width:100vh")));
        QVERIFY(js.contains(QStringLiteral("height:100vw")));
        QVERIFY(js.contains(QStringLiteral("position:fixed")));
    }

    void webpageScript270SwapsBox()
    {
        const QString js = rotation::webpageRotationScript(270);
        QVERIFY(js.contains(QStringLiteral("width:100vh")));
        QVERIFY(js.contains(QStringLiteral("height:100vw")));
    }

    void webpageScript180KeepsBox()
    {
        const QString js = rotation::webpageRotationScript(180);
        QVERIFY(js.contains(QStringLiteral("width:100vw")));
        QVERIFY(js.contains(QStringLiteral("height:100vh")));
        // No portrait-fill repositioning at 180.
        QVERIFY(!js.contains(QStringLiteral("position:fixed")));
    }
};

int runRotationTests(int argc, char** argv)
{
    TestRotation tc;
    return QTest::qExec(&tc, argc, argv);
}

#include "test_rotation.moc"
