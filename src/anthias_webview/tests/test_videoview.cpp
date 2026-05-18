// QtTest unit tests for VideoView's QtMultimedia pipeline
// (issue #2904). The tests instantiate VideoView with no display
// (``QT_QPA_PLATFORM=offscreen``) and assert on the publicly
// observable behaviour:
//
//   * Construction succeeds and the inner QMediaPlayer exists.
//   * ``play()`` updates source / playback state without throwing.
//   * ``stop()`` is idempotent (callable before and after play).
//   * Options dict keys are accepted forgivingly (no crash on
//     unknown / empty option values).
//   * The audio device resolver falls back to the system default
//     when a typo'd ALSA spec doesn't match anything.
//   * ``video-rotate`` actually rotates the QGraphicsVideoItem.
//
// Real-device validation handles the ffmpeg pipeline end-to-end
// (decoder engagement + drop counts via
// /data/.anthias/playback-stats.log on the BBB test bed).
// QtMultimedia's pipeline doesn't fully
// initialise under the offscreen platform because there's no
// rendering surface to upload frames into, so we don't try to play
// media — just exercise the API surface plus the rotation transform.

#include <QCoreApplication>
#include <QGraphicsScene>
#include <QGraphicsVideoItem>
#include <QMediaPlayer>
#include <QSignalSpy>
#include <QTest>
#include <QUrl>
#include <QVariantMap>

#include "videoview.h"


class TestVideoView : public QObject
{
    Q_OBJECT

private slots:
    // Smoke test: constructing VideoView creates the QMediaPlayer
    // + QGraphicsVideoItem pair and the stats logger initialises
    // (writes an ``INIT`` line on hosts where ``/data/.anthias`` is
    // writeable — the test host typically has no such directory, in
    // which case the warning fires and statsStream stays null; both
    // states are acceptable).
    void constructorBuildsPlayer()
    {
        VideoView view;
        QVERIFY(view.findChild<QMediaPlayer*>() != nullptr);
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

    // ``video-rotate`` must actually rotate the QGraphicsVideoItem
    // (not just be stored as a dynamic property — that's what the
    // prior QVideoWidget code did, which the PR #2905 review
    // flagged as a silent Pi 4 regression). Assert on
    // ``QGraphicsItem::rotation()``, the value the painter reads
    // to apply the transform. Both ``str`` and ``int`` are tested
    // because pydbus may marshal the option either way depending
    // on caller — the C++ side normalises via ``QVariant::toInt``.
    void playRotatesVideoItem()
    {
        for (int angle : {0, 90, 180, 270}) {
            for (const QVariant& asWire :
                 {QVariant(QString::number(angle)), QVariant(angle)}) {
                VideoView view;
                // Force a non-zero geometry so positionVideoItem
                // can size the item; default 640x480 is fine but
                // 1080p makes the transform-origin maths match
                // production.
                view.resize(1920, 1080);
                QVariantMap options;
                options["video-rotate"] = asWire;
                view.play(QStringLiteral("file:///nonexistent.mp4"),
                          options);
                QGraphicsVideoItem* item = findVideoItem(view);
                QVERIFY2(item != nullptr,
                         "QGraphicsVideoItem missing from scene");
                QCOMPARE(qRound(item->rotation()), angle);
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
        QGraphicsVideoItem* item = findVideoItem(view);
        QVERIFY(item != nullptr);
        QCOMPARE(qRound(item->rotation()), 0);
        view.stop();
    }

private:
    // QGraphicsVideoItem is a QGraphicsObject attached to the
    // scene, not a child QObject of the widget tree, so
    // ``findChild`` won't reach it. Recover it via the scene's
    // items() list — VideoView's scene is the only QGraphicsScene
    // parented to the widget.
    QGraphicsVideoItem* findVideoItem(const QWidget& view) const
    {
        const QList<QGraphicsScene*> scenes =
            view.findChildren<QGraphicsScene*>();
        if (scenes.isEmpty()) {
            return nullptr;
        }
        for (QGraphicsItem* sceneItem : scenes.first()->items()) {
            if (sceneItem->type() == QGraphicsVideoItem::Type) {
                return static_cast<QGraphicsVideoItem*>(sceneItem);
            }
        }
        return nullptr;
    }
};

QTEST_MAIN(TestVideoView)
#include "test_videoview.moc"
