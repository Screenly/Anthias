// QtTest unit tests for VideoView's QtMultimedia pipeline
// (issue #2904, render path reworked for #2967). The tests
// instantiate VideoView with no display
// (``QT_QPA_PLATFORM=offscreen`` + ``QT_QUICK_BACKEND=software``)
// and assert on the publicly observable behaviour:
//
//   * Construction succeeds, the inner QMediaPlayer exists, and the
//     QML scene loads with a usable VideoOutput item wired to the
//     player's video sink.
//   * ``play()`` updates source / playback state without throwing.
//   * ``stop()`` is idempotent (callable before and after play).
//   * Options dict keys are accepted forgivingly (no crash on
//     unknown / empty option values).
//   * The audio device resolver falls back to the system default
//     when a typo'd ALSA spec doesn't match anything.
//   * ``video-rotate`` lands on the VideoOutput item's
//     ``orientation`` property (the value the scene graph consumes).
//
// Real-device validation handles the ffmpeg pipeline end-to-end
// (decoder engagement + delivered/rendered counts via
// /data/.anthias/playback-stats.log on the BBB test bed).
// QtMultimedia's pipeline doesn't fully initialise under the
// offscreen platform because there's no rendering surface to
// composite into, so we don't try to play media — just exercise the
// API surface plus the option plumbing.

#include <QCoreApplication>
#include <QMediaPlayer>
#include <QQuickItem>
#include <QQuickWidget>
#include <QSignalSpy>
#include <QTest>
#include <QUrl>
#include <QVariantMap>
#include <QVideoSink>

#include "videoview.h"


class TestVideoView : public QObject
{
    Q_OBJECT

private slots:
    // Smoke test: constructing VideoView creates the QMediaPlayer
    // and loads the QML scene with the VideoOutput item present.
    // The stats logger initialises too (writes an ``INIT`` line on
    // hosts where ``/data/.anthias`` is writeable — the test host
    // typically has no such directory, in which case the warning
    // fires and statsStream stays null; both states are acceptable).
    void constructorBuildsPlayer()
    {
        VideoView view;
        QVERIFY(view.findChild<QMediaPlayer*>() != nullptr);
        QVERIFY2(findVideoOutput(view) != nullptr,
                 "VideoOutput item missing — videoview.qml failed to "
                 "load (missing qml6-module-qtquick / "
                 "qml6-module-qtmultimedia on this host?)");
    }

    // The player must render into the VideoOutput's sink — that's
    // the whole point of the #2967 rework. Guard against a silent
    // regression where the QML loads but the sink wiring is dropped
    // (video would decode to nowhere, exactly the failure mode the
    // VLC/mmal era shipped for years).
    void playerUsesVideoOutputSink()
    {
        VideoView view;
        QMediaPlayer* player = view.findChild<QMediaPlayer*>();
        QQuickItem* item = findVideoOutput(view);
        QVERIFY(player != nullptr);
        QVERIFY(item != nullptr);
        QVERIFY(player->videoSink() != nullptr);
        QCOMPARE(
            player->videoSink(),
            qvariant_cast<QVideoSink*>(item->property("videoSink")));
    }

    // ``stop()`` must be callable on a freshly-built VideoView
    // (defensive: asset_loop may call stop() during a
    // rotate-from-image path without ever having played a video on
    // this widget). Also callable repeatedly without crashing.
    void stopIsIdempotent()
    {
        VideoView view;
        view.stop();
        view.stop();
        QVERIFY(true);
    }

    // ``play()`` with an empty options dict shouldn't crash. The
    // URI is non-existent — libavcodec will error out asynchronously
    // via ``errorOccurred`` once it tries to resolve, but the
    // setSource + play call itself must return cleanly.
    void playWithEmptyOptionsDoesNotCrash()
    {
        VideoView view;
        view.play(QStringLiteral("file:///nonexistent.mp4"), QVariantMap());
        view.stop();
        QVERIFY(true);
    }

    // ``audio-device`` option is forgiving: a typo'd ALSA name
    // falls back to the system default audio output rather than
    // crashing.
    void playFallsBackOnUnknownAudioDevice()
    {
        VideoView view;
        QVariantMap options;
        options["audio-device"] = QStringLiteral(
            "alsa/sysdefault:CARD=NotARealCard");
        view.play(QStringLiteral("file:///nonexistent.mp4"), options);
        view.stop();
        QVERIFY(true);
    }

    // ``video-rotate`` must land on the VideoOutput item's
    // ``orientation`` property — the value the scene graph actually
    // consumes when rotating frames (the QGraphicsVideoItem era
    // asserted ``QGraphicsItem::rotation()`` for the same reason:
    // a stored-but-unconsumed dynamic property was PR #2905's silent
    // Pi 4 regression). Both ``str`` and ``int`` are tested because
    // pydbus may marshal the option either way depending on caller —
    // the C++ side normalises via ``QVariant::toInt``.
    void playRotatesVideoOutput()
    {
        for (int angle : {0, 90, 180, 270}) {
            for (const QVariant& asWire :
                 {QVariant(QString::number(angle)), QVariant(angle)}) {
                VideoView view;
                view.resize(1920, 1080);
                QVariantMap options;
                options["video-rotate"] = asWire;
                view.play(QStringLiteral("file:///nonexistent.mp4"),
                          options);
                QQuickItem* item = findVideoOutput(view);
                QVERIFY2(item != nullptr,
                         "VideoOutput item missing from QML scene");
                QCOMPARE(item->property("orientation").toInt(), angle);
                view.stop();
            }
        }
    }

    // Defensive: a non-cardinal angle should snap to 0 so the
    // operator never sees a half-rotated video from a bad D-Bus
    // caller. ``applyRotation`` normalises to {0, 90, 180, 270}.
    void playSnapsBadAngleToZero()
    {
        VideoView view;
        view.resize(1920, 1080);
        QVariantMap options;
        options["video-rotate"] = QStringLiteral("45");
        view.play(QStringLiteral("file:///nonexistent.mp4"), options);
        QQuickItem* item = findVideoOutput(view);
        QVERIFY(item != nullptr);
        QCOMPARE(item->property("orientation").toInt(), 0);
        view.stop();
    }

private:
    // The VideoOutput is a QQuickItem inside the QQuickWidget's QML
    // scene, not a child QObject of the widget tree, so a plain
    // ``findChild`` on the VideoView won't reach it — go through the
    // QQuickWidget's root object.
    QQuickItem* findVideoOutput(const QWidget& view) const
    {
        QQuickWidget* quick = view.findChild<QQuickWidget*>();
        if (!quick || !quick->rootObject()) {
            return nullptr;
        }
        return quick->rootObject()->findChild<QQuickItem*>(
            QStringLiteral("videoOutput"));
    }
};

QTEST_MAIN(TestVideoView)
#include "test_videoview.moc"
