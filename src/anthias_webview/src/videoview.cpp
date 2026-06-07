#include "videoview.h"

#include <QAudioOutput>
#include <QDateTime>
#include <QDebug>
#include <QDir>
#include <QFileInfo>
#include <QMediaDevices>
#include <QMediaMetaData>
#include <QQmlError>
#include <QQuickItem>
#include <QQuickWidget>
#include <QQuickWindow>
#include <QRegularExpression>
#include <QVariant>
#include <QVideoFrame>
#include <QVideoSink>
#include <QtGlobal>


VideoView::VideoView(QWidget* parent) : QWidget(parent)
{
    videoLayout = new QHBoxLayout(this);
    videoLayout->setContentsMargins(0, 0, 0, 0);
    videoLayout->setSpacing(0);

    // QML VideoOutput in a QQuickWidget: frames render through the
    // RHI scene graph (shader YUV→RGB at composite time) instead of
    // the QGraphicsVideoItem toImage()-readback-blit chain that
    // capped presentation at 8–12 fps (issue #2967, see videoview.h).
    // Black backdrop is two layers with distinct jobs: ``clearColor``
    // covers the pre-QML-load window, the QML Rectangle provides the
    // steady-state letterbox fill around PreserveAspectFit. (No
    // widget-palette layer on top — the QQuickWidget fills the whole
    // layout, so a palette would be permanently occluded.)
    quickWidget = new QQuickWidget(this);
    quickWidget->setResizeMode(QQuickWidget::SizeRootObjectToView);
    quickWidget->setClearColor(Qt::black);
    quickWidget->setSource(QUrl(QStringLiteral("qrc:/videoview.qml")));
    if (quickWidget->status() == QQuickWidget::Error) {
        const auto errors = quickWidget->errors();
        for (const QQmlError& error : errors) {
            qWarning() << "VideoView: QML load error:" << error.toString();
        }
    }
    videoLayout->addWidget(quickWidget);

    if (quickWidget->rootObject()) {
        videoOutputItem = quickWidget->rootObject()
                              ->findChild<QQuickItem*>(
                                  QStringLiteral("videoOutput"));
    }
    if (videoOutputItem) {
        // VideoOutput exposes its sink as a property; resolving it
        // here (rather than player->setVideoOutput(item)) keeps an
        // explicit QVideoSink* around for the frame-delivery counter.
        videoSink = qvariant_cast<QVideoSink*>(
            videoOutputItem->property("videoSink"));
    }
    if (!videoOutputItem || !videoSink) {
        // Fail hard rather than limp into decode-but-render-nowhere:
        // a kiosk that silently black-screens every video while its
        // logs read "playing" is the exact failure mode the VLC/mmal
        // era shipped (docs/board-enablement.md, "rendered to
        // nowhere"). Aborting hands the device to the existing
        // spawn-retry / container-restart supervision, which is loud
        // in fleet telemetry. Most likely cause on a device image:
        // qml6-module-qtquick / qml6-module-qtmultimedia missing —
        // the QML import fails at runtime, not the C++ link (see
        // tools/image_builder/utils.py).
        qFatal("VideoView: QML video scene unavailable (videoOutput "
               "item or its videoSink missing — check the QML load "
               "errors above and the qml6-module-qtquick / "
               "qml6-module-qtmultimedia packages). Aborting so the "
               "supervisor restarts the viewer instead of decoding "
               "video to nowhere.");
    }

    player = new QMediaPlayer(this);
    audioOutput = new QAudioOutput(this);
    player->setAudioOutput(audioOutput);

    // The player renders into an intermediate sink rather than the
    // QML item's own — onVideoFrameDelivered() forwards frames to
    // the VideoOutput only when the scene graph has composited the
    // previous one. A 60 fps source otherwise schedules a render per
    // delivery on a GUI thread that sustains ~45 renders/s at 1080p
    // on a Pi 4; that overload presented 22.6 fps with the playback
    // position falling to ~0.6x realtime (issue #2987). The
    // intermediate sink also keeps the decode-side counter
    // (frames-delivered) honest now that the item sink only sees
    // forwarded frames.
    pacingSink = new QVideoSink(this);
    player->setVideoSink(pacingSink);

    connect(player, &QMediaPlayer::playbackStateChanged,
            this, &VideoView::onPlaybackStateChanged);
    connect(player, &QMediaPlayer::mediaStatusChanged,
            this, &VideoView::onMediaStatusChanged);
    connect(player, &QMediaPlayer::errorOccurred,
            this, &VideoView::onErrorOccurred);

    // QVideoSink::videoFrameChanged fires once per decoded frame
    // (after libavcodec / V4L2 drops happen upstream) — the
    // decode-side rate, counted as frames-delivered before the
    // pacing gate decides whether to forward.
    connect(pacingSink, &QVideoSink::videoFrameChanged,
            this, &VideoView::onVideoFrameDelivered);

    // Presentation-side counter. Retried from play() in case the
    // item→window attachment ever lands later than this constructor
    // (qrc setSource is synchronous on Qt 6.8, but a dead counter
    // would silently report frames-rendered=0 — the inverse of the
    // #2967 blind spot — so don't bet the diagnostic on it).
    connectRenderCounter();

    openStatsLog();

    statsTimer = new QTimer(this);
    statsTimer->setInterval(1000);
    connect(statsTimer, &QTimer::timeout, this, &VideoView::sampleStats);
}

VideoView::~VideoView()
{
    if (statsTimer) {
        statsTimer->stop();
    }
    if (player) {
        player->stop();
    }
    if (statsStream) {
        statsStream->flush();
        delete statsStream;
        statsStream = nullptr;
    }
    if (statsFile) {
        statsFile->close();
    }
}

void VideoView::openStatsLog()
{
    // Best-effort observability, not durable history: cap the file
    // at kMaxStatsLogBytes by truncating on viewer start. The
    // truncate-on-restart cadence means a runaway clip (e.g., the
    // 1 Hz SAMPLE writes accumulating during a stuck stream) is
    // bounded across the next process lifecycle, not in real time
    // — which is enough for the 15 GB SD-card constraint #2904's
    // burn-in surfaced.
    QDir().mkpath(QStringLiteral("/data/.anthias"));
    const QString path =
        QStringLiteral("/data/.anthias/playback-stats.log");
    QFile::OpenMode mode =
        QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text;
    if (QFileInfo(path).size() > kMaxStatsLogBytes) {
        // Drop Append → Truncate on overflow.
        mode = QIODevice::WriteOnly | QIODevice::Truncate
               | QIODevice::Text;
    }
    statsFile = new QFile(path, this);
    if (statsFile->open(mode)) {
        statsStream = new QTextStream(statsFile);
        writeStats(
            QStringLiteral("INIT"),
            QStringLiteral(
                "backend=qtmultimedia/ffmpeg sink=quick-videooutput "
                "qt=%1 audio_default=%2")
                .arg(QStringLiteral(QT_VERSION_STR),
                     QMediaDevices::defaultAudioOutput().description()));
    } else {
        qWarning() << "VideoView: could not open" << path
                   << "for stats — playback will run without"
                   << "frame-drop logging.";
        delete statsFile;
        statsFile = nullptr;
    }
}

void VideoView::play(const QString& uri, const QVariantMap& options)
{
    if (!player) {
        qWarning() << "VideoView::play: QMediaPlayer not initialised";
        return;
    }

    // Per-file options. Audio device first so any audible signal
    // hits the right ALSA card from the first frame.
    QStringList summary;
    if (options.contains(QStringLiteral("audio-device"))) {
        const QString alsaSpec =
            options.value(QStringLiteral("audio-device")).toString();
        const QAudioDevice device = resolveAlsaDevice(alsaSpec);
        audioOutput->setDevice(device);
        summary << QStringLiteral("audio-device=%1").arg(alsaSpec);
    }

    // Optional per-item rotation of the VideoOutput item. No board
    // sends ``video-rotate`` any more: every platform now rotates the
    // whole screen (eglfs via QT_QPA_EGLFS_ROTATION on Pi 4, wlroots
    // via wlr-randr on x86) and the Quick scene inherits that
    // transform, so applying it again here would double-rotate. The
    // parse is kept as a defensive no-op (default 0 = applyRotation(0))
    // so an old viewer that still passes the option degrades
    // gracefully rather than erroring on an unknown key.
    int rotation = 0;
    if (options.contains(QStringLiteral("video-rotate"))) {
        bool ok = false;
        rotation =
            options.value(QStringLiteral("video-rotate")).toInt(&ok);
        if (!ok) {
            rotation = 0;
        }
        summary << QStringLiteral("video-rotate=%1").arg(rotation);
    }
    applyRotation(rotation);

    // Backstop for the constructor-time connection — see
    // connectRenderCounter().
    connectRenderCounter();

    currentUri = uri;
    // playStartedAt is RESTARTED on LoadedMedia (not here) so the
    // elapsed-ms window measures real playback time, not decoder
    // init. Reset both frame counters now so the very first counts
    // are clean.
    playStartedAt.invalidate();
    framesDelivered = 0;
    framesForwarded = 0;
    framesRendered = 0;
    containerFps = 0.0;
    sceneReadyForFrame = true;
    pendingFrame = QVideoFrame();
    writeStats(
        QStringLiteral("LOADFILE"),
        QStringLiteral("uri=%1 options={%2}")
            .arg(uri, summary.join(QLatin1Char(' '))));

    // Local-path URIs (e.g. ``/data/anthias_assets/abc.mp4``) come
    // through as scheme-less strings; ``QUrl(uri)`` parses them as
    // relative URLs with no host/scheme and QMediaPlayer refuses
    // to set them as the source. ``QUrl::fromLocalFile`` promotes
    // a path to a proper ``file://`` URL. Anything already
    // carrying a scheme (``http://``, ``file://``, ``rtsp://``)
    // round-trips through ``QUrl(uri)`` untouched.
    const QUrl source = uri.startsWith(QLatin1Char('/'))
                            ? QUrl::fromLocalFile(uri)
                            : QUrl(uri);
    player->setSource(source);
    player->play();
    if (statsTimer) {
        statsTimer->start();
    }
}

void VideoView::stop()
{
    if (!player) {
        return;
    }
    if (statsTimer) {
        statsTimer->stop();
    }
    if (statsStream && !currentUri.isEmpty()) {
        const qint64 elapsedMs =
            playStartedAt.isValid() ? playStartedAt.elapsed() : -1;
        writeStats(
            QStringLiteral("STOP"),
            QStringLiteral(
                "uri=%1 elapsed_ms=%2 frames-delivered=%3 "
                "frames-forwarded=%4 frames-rendered=%5 "
                "position-ms=%6")
                .arg(currentUri)
                .arg(elapsedMs)
                .arg(framesDelivered)
                .arg(framesForwarded)
                .arg(framesRendered)
                .arg(player->position()));
    }
    player->stop();
    // Reset the pacing gate: a frame parked mid-render must not be
    // forwarded by a later afterRendering (stale-frame flash on the
    // next reveal), nor keep its decoder buffer alive between
    // assets. Pushing an empty frame to the VideoOutput releases the
    // last displayed buffer too — black beats a stale frame when the
    // widget is next shown.
    pendingFrame = QVideoFrame();
    sceneReadyForFrame = true;
    if (videoSink) {
        videoSink->setVideoFrame(QVideoFrame());
    }
}

void VideoView::onPlaybackStateChanged(QMediaPlayer::PlaybackState state)
{
    if (state == QMediaPlayer::PlayingState) {
        const QMediaMetaData meta = player->metaData();
        containerFps = meta.value(QMediaMetaData::VideoFrameRate).toReal();
        writeStats(
            QStringLiteral("PLAYING"),
            QStringLiteral(
                "video-codec=%1 resolution=%2 container-fps=%3 "
                "audio-codec=%4 rotation=%5")
                .arg(meta.value(QMediaMetaData::VideoCodec).toString(),
                     meta.value(QMediaMetaData::Resolution)
                         .toSize()
                         .isValid()
                         ? QStringLiteral("%1x%2")
                               .arg(meta.value(QMediaMetaData::Resolution)
                                        .toSize()
                                        .width())
                               .arg(meta.value(QMediaMetaData::Resolution)
                                        .toSize()
                                        .height())
                         : QStringLiteral("?"),
                     QString::number(containerFps),
                     meta.value(QMediaMetaData::AudioCodec).toString(),
                     QString::number(currentRotation)));
    }
}

void VideoView::onMediaStatusChanged(QMediaPlayer::MediaStatus status)
{
    if (status == QMediaPlayer::LoadedMedia
        || status == QMediaPlayer::BufferedMedia) {
        // Start the elapsed-ms clock when QMediaPlayer reports the
        // stream is ready to play, not at play()-time. Per Qt 6
        // docs ``LoadedMedia`` means "metadata available, playback
        // can start" — the actual first decoded frame lands a hair
        // later via ``QVideoSink::videoFrameChanged``, but starting
        // here is close enough (within a few ms) and avoids
        // counting libavcodec init time as wall-clock playback.
        // The decoder init window was 100-200 ms on Pi 4 cold-
        // starts, which inflated drop counts on the first clip
        // after a viewer restart; deferring to here removes that
        // skew. Both ``LoadedMedia`` and ``BufferedMedia`` are
        // accepted so the clock arms whichever fires first (the
        // order varies by backend). Only the FIRST transition
        // starts it (``isValid()`` check) so a mid-clip buffering
        // bounce doesn't reset the counter.
        if (!playStartedAt.isValid()) {
            playStartedAt.start();
        }
    } else if (status == QMediaPlayer::EndOfMedia) {
        const qint64 elapsedMs =
            playStartedAt.isValid() ? playStartedAt.elapsed() : -1;
        // Compare ``frames-delivered`` against the decoder-expected
        // count (container_fps × elapsed_s). The gap = frames
        // dropped on the way to the sink — the same number mpv
        // exposed as ``frame-drop-count``. ``frames-rendered`` is
        // the presentation-side count (scene-graph renders); a
        // rendered count far below delivered = paint-bound, the
        // #2967 failure mode the old log could not see.
        const qreal expected =
            containerFps > 0.0 ? containerFps * (elapsedMs / 1000.0) : -1.0;
        const qint64 dropped =
            expected > 0.0
                ? std::max<qint64>(0, qRound(expected) - framesDelivered)
                : -1;
        writeStats(
            QStringLiteral("END_FILE"),
            QStringLiteral(
                "uri=%1 elapsed_ms=%2 frames-delivered=%3 "
                "frames-forwarded=%4 frames-rendered=%5 "
                "expected=%6 dropped=%7")
                .arg(currentUri)
                .arg(elapsedMs)
                .arg(framesDelivered)
                .arg(framesForwarded)
                .arg(framesRendered)
                .arg(qRound(expected))
                .arg(dropped));
        if (statsTimer) {
            statsTimer->stop();
        }
        emit videoEnded();
    } else if (status == QMediaPlayer::InvalidMedia) {
        writeStats(
            QStringLiteral("INVALID_MEDIA"),
            QStringLiteral("uri=%1").arg(currentUri));
    }
}

void VideoView::onErrorOccurred(
    QMediaPlayer::Error error, const QString& message)
{
    writeStats(
        QStringLiteral("ERROR"),
        QStringLiteral("uri=%1 code=%2 message=%3")
            .arg(currentUri, QString::number(static_cast<int>(error)),
                 message));
    qWarning() << "VideoView::onErrorOccurred:" << error << message;
}

void VideoView::sampleStats()
{
    if (!player || !statsStream) {
        return;
    }
    const qint64 posMs = player->position();
    const qint64 elapsedMs =
        playStartedAt.isValid() ? playStartedAt.elapsed() : -1;
    const qreal expected =
        containerFps > 0.0 ? containerFps * (elapsedMs / 1000.0) : -1.0;
    const qint64 dropped =
        expected > 0.0
            ? std::max<qint64>(0, qRound(expected) - framesDelivered)
            : -1;
    writeStats(
        QStringLiteral("SAMPLE"),
        QStringLiteral(
            "position-ms=%1 frames-delivered=%2 frames-forwarded=%3 "
            "frames-rendered=%4 expected=%5 dropped=%6")
            .arg(posMs)
            .arg(framesDelivered)
            .arg(framesForwarded)
            .arg(framesRendered)
            .arg(qRound(expected))
            .arg(dropped));
}

void VideoView::onVideoFrameDelivered(const QVideoFrame& frame)
{
    ++framesDelivered;
    if (!videoSink) {
        return;
    }
    if (!frame.isValid()) {
        // Stream end / source change marker — always forward so the
        // VideoOutput clears instead of freezing on the last frame.
        pendingFrame = QVideoFrame();
        videoSink->setVideoFrame(frame);
        return;
    }
    // Gate only when the render counter is actually wired: without
    // afterRendering firing, sceneReadyForFrame would never re-arm
    // and the video would freeze on its first frame. In that
    // (shouldn't-happen) state, fall back to unpaced forwarding —
    // the pre-#2987 behaviour.
    if (renderCounterConnection && !sceneReadyForFrame) {
        // Scene busy — park the frame in the single-slot mailbox
        // (replacing any older parked frame) so onSceneRendered()
        // can forward the freshest one the moment the render
        // finishes. Without the mailbox the gate was stop-and-wait:
        // render (~21 ms) → re-arm → idle until the NEXT delivery
        // (≤16 ms at 60 fps) → render, which measured only ~23
        // presented fps on a GUI thread that renders ~45/s when
        // back-to-back.
        pendingFrame = frame;
        return;
    }
    sceneReadyForFrame = false;
    ++framesForwarded;
    videoSink->setVideoFrame(frame);
}

void VideoView::onSceneRendered()
{
    ++framesRendered;
    if (pendingFrame.isValid() && videoSink) {
        // Chain straight into the next render with the freshest
        // parked frame — keeps the gate closed.
        ++framesForwarded;
        videoSink->setVideoFrame(pendingFrame);
        pendingFrame = QVideoFrame();
        return;
    }
    sceneReadyForFrame = true;
}

void VideoView::connectRenderCounter()
{
    // Count scene-graph renders — the presentation-side rate. The
    // Quick scene re-renders only on damage, and during playback the
    // VideoOutput frame updates are the damage, so renders/s ≈
    // frames actually composited to the screen. This is the counter
    // whose absence let #2967's 8 fps presentation ship while the
    // sink-side log read "dropped≈0". Idempotent: no-op once the
    // connection is made (constructor normally succeeds; play()
    // retries as a backstop against late item→window attachment).
    if (renderCounterConnection || !videoOutputItem) {
        return;
    }
    QQuickWindow* window = videoOutputItem->window();
    if (!window) {
        return;
    }
    renderCounterConnection =
        connect(window, &QQuickWindow::afterRendering,
                this, &VideoView::onSceneRendered);
}

QAudioDevice VideoView::resolveAlsaDevice(const QString& alsaSpec) const
{
    // The Python side passes a full ALSA spec like
    // ``alsa/sysdefault:CARD=vc4hdmi0``. ``QAudioDevice::id()`` on
    // the ALSA backend is shorter — typically just the card name
    // (``vc4hdmi0``) or a ``plughw:CARD=<name>,DEV=0`` style
    // string. A plain ``id.contains(fullSpec)`` substring match
    // therefore almost always failed and silently fell back to
    // ``defaultAudioOutput`` (review of PR #2905 flagged this on
    // multi-HDMI Pi 4 / Pi 5 where the default might land on the
    // wrong HDMI port). Extract the ``CARD=<name>`` segment
    // specifically and match against that — that's the
    // discriminator ALSA itself uses.
    QString cardName;
    static const QRegularExpression cardRe(
        QStringLiteral("CARD=([A-Za-z0-9_-]+)"));
    QRegularExpressionMatch match = cardRe.match(alsaSpec);
    if (match.hasMatch()) {
        cardName = match.captured(1);
    } else {
        // Fallback: strip the ``alsa/`` prefix and use whatever is
        // left (e.g. ``default``).
        cardName = alsaSpec;
        if (cardName.startsWith(QLatin1String("alsa/"))) {
            cardName = cardName.mid(5);
        }
    }

    const QList<QAudioDevice> devices = QMediaDevices::audioOutputs();
    if (!cardName.isEmpty() && cardName != QLatin1String("default")) {
        for (const QAudioDevice& dev : devices) {
            const QString id = QString::fromUtf8(dev.id());
            if (id.contains(cardName, Qt::CaseInsensitive)
                || dev.description().contains(cardName, Qt::CaseInsensitive)) {
                qInfo().nospace()
                    << "VideoView::resolveAlsaDevice: spec=" << alsaSpec
                    << " resolved CARD=" << cardName
                    << " to QAudioDevice id=" << id
                    << " (\"" << dev.description() << "\")";
                return dev;
            }
        }
        qWarning() << "VideoView::resolveAlsaDevice: no QAudioDevice"
                   << "matched CARD=" << cardName
                   << "from spec" << alsaSpec
                   << "— falling back to default";
    }
    const QAudioDevice fallback = QMediaDevices::defaultAudioOutput();
    qInfo().nospace()
        << "VideoView::resolveAlsaDevice: spec=" << alsaSpec
        << " using default QAudioDevice id="
        << QString::fromUtf8(fallback.id())
        << " (\"" << fallback.description() << "\")";
    return fallback;
}

void VideoView::applyRotation(int angle)
{
    // Normalise to {0, 90, 180, 270}. Anything else snaps to 0
    // (defensive — Python side already clamps via
    // ``clamp_screen_rotation`` but the D-Bus surface trusts no
    // caller).
    int normalised = ((angle % 360) + 360) % 360;
    if (normalised != 0 && normalised != 90
        && normalised != 180 && normalised != 270) {
        normalised = 0;
    }
    currentRotation = normalised;
    if (!videoOutputItem) {
        return;
    }
    // VideoOutput consumes ``orientation`` natively (it exists for
    // camera-orientation use): the scene graph rotates the frames
    // and swaps the fit box for 90/270 — no manual transform-origin
    // or viewport-transpose bookkeeping like the QGraphicsVideoItem
    // era needed.
    videoOutputItem->setProperty("orientation", normalised);
}

void VideoView::writeStats(const QString& kind, const QString& detail)
{
    if (!statsStream) {
        return;
    }
    *statsStream << QDateTime::currentDateTimeUtc().toString(Qt::ISODate)
                 << QLatin1Char(' ') << kind
                 << QLatin1Char(' ') << detail
                 << QLatin1Char('\n');
    statsStream->flush();
}
