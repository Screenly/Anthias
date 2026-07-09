#include "videoview.h"

#include <QAudioOutput>
#include <QDateTime>
#include <QDebug>
#include <QDir>
#include <QFileInfo>
#include <QMediaDevices>
#include <QMediaMetaData>
#include <QPaintEvent>
#include <QPainter>
#include <QQmlError>
#include <QQuickItem>
#include <QQuickWidget>
#include <QQuickWindow>
#include <QRegularExpression>
#include <QVariant>
#include <QVideoFrame>
#include <QVideoSink>
#include <QtGlobal>

#ifdef ANTHIAS_GSTREAMER
#include <gst/app/gstappsink.h>
#include <gst/gst.h>
#include <gst/video/video.h>

#include <QGuiApplication>
#include <QScreen>
#include <qpa/qplatformnativeinterface.h>

#include <xf86drm.h>
#include <xf86drmMode.h>

#include <cstring>
#endif


// CPU-raster video surface for boards whose GPU can't present the QML
// VideoOutput RHI path (pi3-64 / VideoCore IV — issue #3084). Holds the
// latest decoded frame as a QImage and blits it, aspect-fit on black,
// through the widget backing store — the same compositing path images
// and webpages use, which those boards DO scan out. No Q_OBJECT: it
// carries no signals/slots and calls back into VideoView (its
// friend-owner) directly from paintEvent to re-arm the pacing gate.
class RasterVideoWidget : public QWidget
{
public:
    explicit RasterVideoWidget(VideoView* owner)
        : QWidget(owner), owner_(owner)
    {
        // The whole surface is repainted (black fill + frame) every
        // paint, so suppress the default background clear and the
        // pre-first-frame palette flash.
        setAttribute(Qt::WA_OpaquePaintEvent);
    }

    void setImage(const QImage& image)
    {
        image_ = image;
        update();
    }

    void clear()
    {
        image_ = QImage();
        update();
    }

protected:
    void paintEvent(QPaintEvent*) override
    {
        QPainter painter(this);
        painter.fillRect(rect(), Qt::black);
        if (!image_.isNull()) {
            // Aspect-fit letterbox — matches VideoOutput's
            // PreserveAspectFit. Nearest-neighbour scale (no
            // SmoothPixmapTransform): a 1080p frame on a 1080p panel is
            // ~1:1 and smooth scaling is too costly for the VideoCore IV
            // GUI thread this path exists to serve.
            const QSize target =
                image_.size().scaled(size(), Qt::KeepAspectRatio);
            QRect dst(QPoint(0, 0), target);
            dst.moveCenter(rect().center());
            painter.drawImage(dst, image_);
        }
        owner_->onRasterPainted();
    }

private:
    VideoView* owner_;
    QImage image_;
};


VideoView::VideoView(QWidget* parent) : QWidget(parent)
{
    videoLayout = new QHBoxLayout(this);
    videoLayout->setContentsMargins(0, 0, 0, 0);
    videoLayout->setSpacing(0);

    // Board-selected presentation substrate. ANTHIAS_VIDEO_RASTER is
    // set by the Python side for pi3-64 only (see _build_webview_env):
    // that board's VideoCore IV / GLES2 GPU can't scan out the QML
    // VideoOutput RHI path (issue #3084), so it uses the CPU-raster
    // widget instead. Every other board keeps the fast on-GPU path.
    rasterMode = qEnvironmentVariableIntValue("ANTHIAS_VIDEO_RASTER") != 0;

#ifdef ANTHIAS_GSTREAMER
    // On pi3-64 the raster widget is fed by the in-process GStreamer HW
    // pipeline (gstPlay), not QMediaPlayer+toImage — the ISP converts in
    // hardware. gst_init is idempotent; safe to call unconditionally on
    // this board.
    gstMode = rasterMode;
    if (gstMode) {
        gst_init(nullptr, nullptr);
        // Prefer the HW overlay-plane path when requested; gstPlay falls
        // back to the appsink→raster blit if the DRM resources or a free
        // overlay plane can't be resolved at play time.
        gstOverlayMode =
            qEnvironmentVariableIntValue("ANTHIAS_VIDEO_OVERLAY") != 0;
    }
#endif

    if (rasterMode) {
        // No QQuickWidget / VideoOutput / videoSink here: frames are
        // converted with QVideoFrame::toImage() in
        // onVideoFrameDelivered() and blitted by rasterWidget. See
        // videoview.h and RasterVideoWidget above.
        rasterWidget = new RasterVideoWidget(this);
        videoLayout->addWidget(rasterWidget);
    } else {
        // QML VideoOutput in a QQuickWidget: frames render through the
        // RHI scene graph (shader YUV→RGB at composite time) instead of
        // the QGraphicsVideoItem toImage()-readback-blit chain that
        // capped presentation at 8–12 fps (issue #2967, see
        // videoview.h). Black backdrop is two layers with distinct
        // jobs: ``clearColor`` covers the pre-QML-load window, the QML
        // Rectangle provides the steady-state letterbox fill around
        // PreserveAspectFit. (No widget-palette layer on top — the
        // QQuickWidget fills the whole layout, so a palette would be
        // permanently occluded.)
        quickWidget = new QQuickWidget(this);
        quickWidget->setResizeMode(QQuickWidget::SizeRootObjectToView);
        quickWidget->setClearColor(Qt::black);
        quickWidget->setSource(QUrl(QStringLiteral("qrc:/videoview.qml")));
        if (quickWidget->status() == QQuickWidget::Error) {
            const auto errors = quickWidget->errors();
            for (const QQmlError& error : errors) {
                qWarning()
                    << "VideoView: QML load error:" << error.toString();
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
    // #2967 blind spot — so don't bet the diagnostic on it). The
    // raster path has no QQuickWindow; it counts frames-rendered in
    // RasterVideoWidget::paintEvent via onRasterPainted() instead.
    if (!rasterMode) {
        connectRenderCounter();
    }

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
#ifdef ANTHIAS_GSTREAMER
    gstStop();
#endif
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
    QString backend = QStringLiteral("qtmultimedia/ffmpeg");
    QString sinkName = rasterMode ? QStringLiteral("raster-cpu")
                                  : QStringLiteral("quick-videooutput");
#ifdef ANTHIAS_GSTREAMER
    if (gstMode) {
        backend = QStringLiteral("gstreamer/v4l2");
        sinkName = QStringLiteral("gst-appsink");
    }
#endif
    statsFile = new QFile(path, this);
    if (statsFile->open(mode)) {
        statsStream = new QTextStream(statsFile);
        writeStats(
            QStringLiteral("INIT"),
            QStringLiteral(
                "backend=%1 sink=%2 qt=%3 audio_default=%4")
                .arg(backend, sinkName, QStringLiteral(QT_VERSION_STR),
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
    setContainerFps(0.0);
    sceneReadyForFrame = true;
    pendingFrame = QVideoFrame();
    writeStats(
        QStringLiteral("LOADFILE"),
        QStringLiteral("uri=%1 options={%2}")
            .arg(uri, summary.join(QLatin1Char(' '))));

#ifdef ANTHIAS_GSTREAMER
    if (gstMode) {
        // pi3-64: hand off to the GStreamer HW pipeline instead of
        // QMediaPlayer. Frames arrive via onGstFrame → rasterWidget.
        // gstPlay exhausts every GStreamer path (overlay → appsink); if it
        // still fails there is no QMediaPlayer fallback on this board (its
        // VideoOutput black-screens on VideoCore IV, issue #3084), so log
        // loudly — the supervisor's respawn is the recovery.
        if (gstPlay(uri, options)) {
            if (statsTimer) {
                statsTimer->start();
            }
        } else {
            // Don't run the sampler with no pipeline — it would emit SAMPLE
            // lines with position=-1/elapsed=-1 and bury the real failure.
            qWarning() << "VideoView::play: GStreamer playback failed for"
                       << uri << "(no QMediaPlayer fallback on this board)";
        }
        return;
    }
#endif

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
#ifdef ANTHIAS_GSTREAMER
    if (gstMode) {
        if (statsTimer) {
            statsTimer->stop();
        }
        if (statsStream && !currentUri.isEmpty()) {
            const qint64 elapsedMs =
                playStartedAt.isValid() ? playStartedAt.elapsed() : -1;
            // Log the real pipeline position (the pipeline is still up here
            // — gstStop() runs below), not a hard-coded 0 that reads like
            // playback never advanced. -1 if the query fails.
            gint64 posNs = 0;
            const qint64 posMs =
                (gstPipeline
                 && gst_element_query_position(
                        gstPipeline, GST_FORMAT_TIME, &posNs))
                    ? posNs / GST_MSECOND
                    : -1;
            // On the overlay path frames never touch the CPU counters
            // (framesDelivered/forwarded/rendered stay 0); the buffer-probe
            // tally gstRawSamples is the real rendered count, so report it.
            const qint64 rendered = gstOverlayActive
                                        ? gstRawSamples.loadRelaxed()
                                        : framesRendered;
            writeStats(
                QStringLiteral("STOP"),
                QStringLiteral(
                    "uri=%1 elapsed_ms=%2 frames-delivered=%3 "
                    "frames-forwarded=%4 frames-rendered=%5 position-ms=%6")
                    .arg(currentUri)
                    .arg(elapsedMs)
                    .arg(framesDelivered)
                    .arg(framesForwarded)
                    .arg(rendered)
                    .arg(posMs));
        }
        gstStop();
        rasterReady = true;
        if (rasterWidget) {
            rasterWidget->clear();
        }
        return;
    }
#endif
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
    // widget is next shown. The raster path clears rasterWidget for the
    // same reason.
    pendingFrame = QVideoFrame();
    sceneReadyForFrame = true;
    rasterReady = true;
    if (rasterMode) {
        if (rasterWidget) {
            rasterWidget->clear();
        }
    } else if (videoSink) {
        videoSink->setVideoFrame(QVideoFrame());
    }
}

void VideoView::onPlaybackStateChanged(QMediaPlayer::PlaybackState state)
{
    if (state == QMediaPlayer::PlayingState) {
        const QMediaMetaData meta = player->metaData();
        setContainerFps(meta.value(QMediaMetaData::VideoFrameRate).toReal());
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
                     QString::number(containerFps()),
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
            containerFps() > 0.0 ? containerFps() * (elapsedMs / 1000.0) : -1.0;
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
#ifdef ANTHIAS_GSTREAMER
    if (gstOverlayActive) {
        if (!statsStream) {
            return;
        }
        // Overlay path: frames never touch the CPU, so rendered = the
        // sink-pad buffer probe count, expected = source_fps × elapsed,
        // and dropped = the shortfall. kmssink's own late-frame drops are
        // logged separately as QOS lines from the bus watch.
        gint64 posNs = 0;
        const qint64 posMs =
            gst_element_query_position(gstPipeline, GST_FORMAT_TIME, &posNs)
                ? posNs / GST_MSECOND
                : -1;
        const qint64 elapsedMs =
            playStartedAt.isValid() ? playStartedAt.elapsed() : -1;
        const qint64 rendered = gstRawSamples.loadRelaxed();
        const qreal expected =
            containerFps() > 0.0 ? containerFps() * (elapsedMs / 1000.0) : -1.0;
        const qint64 dropped =
            expected > 0.0 ? std::max<qint64>(0, qRound(expected) - rendered)
                           : -1;
        writeStats(
            QStringLiteral("SAMPLE"),
            QStringLiteral("position-ms=%1 frames-rendered=%2 expected=%3 "
                           "dropped=%4 container-fps=%5")
                .arg(posMs)
                .arg(rendered)
                .arg(qRound(expected))
                .arg(dropped)
                .arg(QString::number(containerFps(), 'f', 2)));
        return;
    }
#endif
    if (!player || !statsStream) {
        return;
    }
    // Position source: on the gstMode appsink-raster fallback QMediaPlayer
    // isn't the one playing, so query the GStreamer pipeline; only the
    // plain QtMultimedia path reads player->position().
    qint64 posMs = -1;
#ifdef ANTHIAS_GSTREAMER
    if (gstMode && gstPipeline) {
        gint64 posNs = 0;
        posMs =
            gst_element_query_position(gstPipeline, GST_FORMAT_TIME, &posNs)
                ? posNs / GST_MSECOND
                : -1;
    } else
#endif
    {
        posMs = player->position();
    }
    const qint64 elapsedMs =
        playStartedAt.isValid() ? playStartedAt.elapsed() : -1;
    const qreal expected =
        containerFps() > 0.0 ? containerFps() * (elapsedMs / 1000.0) : -1.0;
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

    // Raster instrumentation: average QVideoFrame::toImage() cost over
    // the frames converted in this 1 s window. Reset the accumulators
    // so each RASTER line is a fresh per-second average.
    if (rasterMode && rasterConvertCount > 0) {
        const double avgMs =
            (rasterConvertUsAccum / 1000.0) / rasterConvertCount;
        writeStats(
            QStringLiteral("RASTER"),
            QStringLiteral("convert-avg-ms=%1 n=%2")
                .arg(QString::number(avgMs, 'f', 1))
                .arg(rasterConvertCount));
        rasterConvertUsAccum = 0;
        rasterConvertCount = 0;
    }

#ifdef ANTHIAS_GSTREAMER
    if (gstMode) {
        // Cumulative appsink deliveries (pipeline's own rate). Compared
        // against SAMPLE frames-rendered (paint rate) this separates a
        // pipeline capped at N fps from a pipeline producing more than
        // the eglfs paint can present.
        writeStats(
            QStringLiteral("GSTRAW"),
            QStringLiteral("appsink-total=%1")
                .arg(gstRawSamples.loadRelaxed()));
    }
#endif
}

void VideoView::onVideoFrameDelivered(const QVideoFrame& frame)
{
    ++framesDelivered;

    if (rasterMode) {
        if (!frame.isValid()) {
            // Stream end / source change marker — drop any parked frame
            // and clear the surface to black instead of freezing on the
            // last one.
            pendingFrame = QVideoFrame();
            if (rasterWidget) {
                rasterWidget->clear();
            }
            return;
        }
        if (!rasterReady) {
            // Paint of the previous frame still in flight — park the
            // freshest frame (replacing any older parked one) for
            // onRasterPainted() to forward. Self-paces the
            // toImage()+blit to the widget's paint rate, which is what
            // bounds this path on VideoCore IV.
            pendingFrame = frame;
            return;
        }
        presentRasterFrame(frame);
        return;
    }

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

void VideoView::presentRasterFrame(const QVideoFrame& frame)
{
    // Close the one-frame gate, convert the decoded frame to a CPU
    // QImage (a GPU→CPU readback for HW-decoded frames — the cost this
    // path trades for actually reaching the VideoCore IV scanout), and
    // hand it to rasterWidget, whose paintEvent blits it and calls
    // onRasterPainted() to re-open the gate. A null conversion (e.g. a
    // frame that can't be mapped) paints black for that frame but still
    // re-arms, so it degrades to a dropped frame rather than a freeze.
    rasterReady = false;
    ++framesForwarded;
    QElapsedTimer convertTimer;
    convertTimer.start();
    const QImage image = frame.toImage();
    rasterConvertUsAccum += convertTimer.nsecsElapsed() / 1000;
    ++rasterConvertCount;
    if (rasterWidget) {
        rasterWidget->setImage(image);
    }
}

void VideoView::onRasterPainted()
{
    ++framesRendered;
    if (pendingFrame.isValid()) {
        // A newer frame arrived mid-paint — show it right away and keep
        // the gate closed so delivery stays paced to paint capacity.
        const QVideoFrame frame = pendingFrame;
        pendingFrame = QVideoFrame();
        presentRasterFrame(frame);
        return;
    }
    rasterReady = true;
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

#ifdef ANTHIAS_GSTREAMER

namespace {

// appsink pulled a frame on a GStreamer streaming thread. Wrap the
// ISP-converted RGB16 buffer as a QImage (deep copy — the GstBuffer is
// released on return), stash it as the newest frame, and post a single
// coalesced onGstFrame() to the GUI thread. If a drain is already queued
// we only overwrite the stashed frame (keeping the freshest) — so a GUI
// thread slower than the pipeline drops intermediate frames rather than
// piling QImages in the event queue and OOM'ing the 1 GB board.
GstFlowReturn anthias_gst_on_new_sample(GstAppSink* sink, gpointer user_data)
{
    GstSample* sample = gst_app_sink_pull_sample(sink);
    if (!sample) {
        return GST_FLOW_OK;
    }
    GstCaps* caps = gst_sample_get_caps(sample);
    GstBuffer* buffer = gst_sample_get_buffer(sample);
    GstVideoInfo info;
    GstMapInfo map;
    if (caps && buffer && gst_video_info_from_caps(&info, caps)
        && gst_buffer_map(buffer, &map, GST_MAP_READ)) {
        const int width = GST_VIDEO_INFO_WIDTH(&info);
        const int height = GST_VIDEO_INFO_HEIGHT(&info);
        const int stride = GST_VIDEO_INFO_PLANE_STRIDE(&info, 0);
        // GStreamer RGB16 is RGB565 — a byte-for-byte match for
        // QImage::Format_RGB16, so wrapping needs no channel swizzle.
        // copy() detaches from the soon-to-be-unmapped GstBuffer.
        const QImage wrapped(
            map.data, width, height, stride, QImage::Format_RGB16);
        const QImage frame = wrapped.copy();
        gst_buffer_unmap(buffer, &map);
        auto* view = static_cast<VideoView*>(user_data);
        view->latchSourceFps(
            GST_VIDEO_INFO_FPS_N(&info), GST_VIDEO_INFO_FPS_D(&info));
        view->pushGstFrame(frame);
    }
    gst_sample_unref(sample);
    return GST_FLOW_OK;
}

// Find a free OVERLAY plane usable on ``crtcId``. The overlay-plane path
// (kmssink) needs an explicit plane-id — otherwise kmssink grabs the
// primary plane, which eglfs already scans out. Skips planes already
// bound to a CRTC (eglfs's primary) and any non-overlay (primary/cursor)
// type. Returns 0 if none found (caller falls back to the raster path).
uint32_t anthias_find_overlay_plane(int fd, uint32_t crtcId)
{
    drmModeRes* res = drmModeGetResources(fd);
    int crtcIndex = -1;
    if (res) {
        for (int i = 0; i < res->count_crtcs; ++i) {
            if (res->crtcs[i] == crtcId) {
                crtcIndex = i;
                break;
            }
        }
        drmModeFreeResources(res);
    }
    if (crtcIndex < 0) {
        return 0;
    }

    drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1);
    drmModePlaneRes* planes = drmModeGetPlaneResources(fd);
    if (!planes) {
        return 0;
    }
    uint32_t chosen = 0;
    for (uint32_t i = 0; i < planes->count_planes && chosen == 0; ++i) {
        drmModePlane* plane = drmModeGetPlane(fd, planes->planes[i]);
        if (!plane) {
            continue;
        }
        const bool crtcCapable = (plane->possible_crtcs >> crtcIndex) & 1u;
        const bool free = plane->crtc_id == 0;
        if (crtcCapable && free) {
            drmModeObjectProperties* props = drmModeObjectGetProperties(
                fd, plane->plane_id, DRM_MODE_OBJECT_PLANE);
            if (props) {
                for (uint32_t p = 0; p < props->count_props; ++p) {
                    drmModePropertyRes* prop =
                        drmModeGetProperty(fd, props->props[p]);
                    if (!prop) {
                        continue;
                    }
                    if (std::strcmp(prop->name, "type") == 0
                        && props->prop_values[p] == DRM_PLANE_TYPE_OVERLAY) {
                        chosen = plane->plane_id;
                    }
                    drmModeFreeProperty(prop);
                }
                drmModeFreeObjectProperties(props);
            }
        }
        drmModeFreePlane(plane);
    }
    drmModeFreePlaneResources(planes);
    return chosen;
}

// Buffer probe on kmssink's sink pad: counts frames that reach the sink
// (the presentation rate for the overlay path, since there is no appsink)
// and captures the source framerate once from the caps so sampleStats can
// compute expected-vs-dropped. Runs on a streaming thread — both the
// frame counter and containerFps (via setContainerFps) are atomic.
GstPadProbeReturn anthias_gst_kms_probe(GstPad* pad, GstPadProbeInfo* /*info*/,
                                        gpointer user_data)
{
    static_cast<VideoView*>(user_data)->onOverlayBuffer(pad);
    return GST_PAD_PROBE_OK;
}

// Pipeline bus watch for the overlay path (kmssink has no appsink EOS
// callback). Serviced on the GUI thread via Qt's GLib event dispatcher.
// Loops the clip on EOS; logs kmssink QoS drop stats and errors.
// Bus watch for the separate audio pipeline. Audio is best-effort: on EOS
// loop it to stay roughly aligned with the looping video; on error tear it
// down (a bad audio device/codec must not take the video with it).
gboolean anthias_gst_audio_bus(GstBus* /*bus*/, GstMessage* message,
                               gpointer user_data)
{
    auto* view = static_cast<VideoView*>(user_data);
    switch (GST_MESSAGE_TYPE(message)) {
    case GST_MESSAGE_EOS:
        view->gstLoopAudio();
        break;
    case GST_MESSAGE_ERROR: {
        GError* err = nullptr;
        gchar* dbg = nullptr;
        gst_message_parse_error(message, &err, &dbg);
        view->logAudio(QStringLiteral("error=%1")
                           .arg(err ? err->message : QStringLiteral("?")));
        if (err) {
            g_error_free(err);
        }
        g_free(dbg);
        view->gstStopAudio();
        break;
    }
    default:
        break;
    }
    return TRUE;
}

gboolean anthias_gst_bus_loop(GstBus* /*bus*/, GstMessage* message,
                              gpointer user_data)
{
    auto* view = static_cast<VideoView*>(user_data);
    switch (GST_MESSAGE_TYPE(message)) {
    case GST_MESSAGE_EOS:
        view->gstRestartLoop();
        break;
    case GST_MESSAGE_QOS: {
        // kmssink's own accounting: authoritative rendered vs dropped.
        guint64 rendered = 0;
        guint64 dropped = 0;
        GstFormat format = GST_FORMAT_UNDEFINED;
        gst_message_parse_qos_stats(message, &format, &rendered, &dropped);
        view->logOverlayQos(rendered, dropped);
        break;
    }
    case GST_MESSAGE_ERROR: {
        GError* err = nullptr;
        gchar* dbg = nullptr;
        gst_message_parse_error(message, &err, &dbg);
        qWarning() << "VideoView pipeline error:"
                   << (err ? err->message : "?") << (dbg ? dbg : "");
        if (err) {
            g_error_free(err);
        }
        g_free(dbg);
        // Don't sit on a stale frame with a dead pipeline: tear it down on
        // the GUI thread (the next asset re-shows and rebuilds). Return
        // FALSE to remove this bus watch — gstStop() also removes it, which
        // is a harmless no-op.
        QMetaObject::invokeMethod(
            view, [view] { view->stop(); }, Qt::QueuedConnection);
        return FALSE;
    }
    default:
        break;
    }
    return TRUE;
}

}  // namespace

bool VideoView::gstPlay(const QString& uri, const QVariantMap& options)
{
    gstStop();
    // Reset the raw frame counter here (covers BOTH the overlay and the
    // appsink paths) so a new asset's stats don't carry over the previous
    // one's cumulative count.
    gstRawSamples.storeRelaxed(0);
    // Clear the on-screen raster surface so the previous asset's last frame
    // can't linger (stale-frame flash) while the new pipeline prerolls or
    // if it fails to start. The overlay path re-clears it too (the video is
    // on the plane, not this widget).
    if (rasterWidget) {
        rasterWidget->clear();
    }

    // filesrc needs a filesystem path. Anthias passes bare local paths
    // (e.g. /data/anthias_assets/<id>) today; normalise a file:// URL
    // defensively so a future caller can't yield a broken location=.
    // Per-pipeline ``"``/``\`` escaping happens where each filesrc is
    // built below.
    QString localPath = uri;
    if (localPath.startsWith(QLatin1String("file://"))) {
        localPath = QUrl(uri).toLocalFile();
    }
    // Resolve the audio device once and start audio HERE (not inside
    // gstPlayOverlay) so BOTH the overlay and the appsink-raster fallback
    // get sound — previously the raster path played silent. Same
    // audio-device option the QMediaPlayer path uses; strip the Qt
    // ``alsa/`` scheme prefix for ALSA.
    QString alsaDev =
        options.value(QStringLiteral("audio-device")).toString();
    if (alsaDev.startsWith(QLatin1String("alsa/"))) {
        alsaDev = alsaDev.mid(5);
    }

    // HW overlay-plane path first (if requested and resolvable): it scans
    // out the video directly on a vc4 overlay plane, bypassing the eglfs
    // GL compositor that caps at ~9 fps. Falls through to the appsink →
    // raster blit below on any failure.
    if (gstOverlayMode && gstPlayOverlay(localPath)) {
        gstStartAudio(localPath, alsaDev);
        return true;
    }

    // Explicit hardware pipeline. pi3-64 only ever receives H.264/MP4
    // (the codec gate rejects every other codec for this board), so an
    // explicit qtdemux ! h264parse ! v4l2h264dec chain is safe and
    // *guarantees* the bcm2835 hardware decoder — letting decodebin pick
    // would risk the software avdec_h264, which can't sustain realtime and
    // thermally reboots the 1 GB board. v4l2convert is the bcm2835 ISP
    // doing SAND→RGB16 in hardware (the conversion the CPU can't afford,
    // ~600 ms/frame). sync=false delivers frames as soon as the ISP emits
    // them (the GUI-side coalescing gate in pushGstFrame paces what
    // actually paints); drop=true / max-buffers=2 bounds latency to the
    // freshest frame.
    QString location = localPath;
    location.replace(QLatin1Char('\\'), QLatin1String("\\\\"));
    location.replace(QLatin1Char('"'), QLatin1String("\\\""));
    // Optional ISP downscale (ANTHIAS_GST_SCALE="WxH", e.g. 1280x720):
    // the bcm2835 ISP scales in hardware, so a smaller output cuts the
    // ISP's per-frame work (and, if the display substrate uploads a
    // smaller texture, the composite cost too). Empty = native source
    // resolution. Experimental knob for the pi3-64 fps tuning.
    QString scaleCaps;
    const QString scale = qEnvironmentVariable("ANTHIAS_GST_SCALE");
    static const QRegularExpression scaleRe(
        QStringLiteral("^(\\d+)x(\\d+)$"));
    const QRegularExpressionMatch scaleMatch = scaleRe.match(scale);
    if (scaleMatch.hasMatch()) {
        scaleCaps = QStringLiteral(",width=%1,height=%2")
                        .arg(scaleMatch.captured(1), scaleMatch.captured(2));
    }

    // A queue after v4l2convert runs decode+ISP on their own thread,
    // decoupled from appsink delivery (leaky=downstream drops the oldest
    // buffered frame if the GUI falls behind, keeping the pipeline
    // free-running). sync=false delivers frames as soon as the ISP emits
    // them rather than clock-pacing in the sink — the GUI-side coalescing
    // gate (pushGstFrame) bounds what actually reaches the paint.
    const QString description =
        QStringLiteral(
            "filesrc location=\"%1\" ! qtdemux ! h264parse ! v4l2h264dec ! "
            "v4l2convert ! video/x-raw,format=RGB16%2 ! "
            "queue max-size-buffers=3 leaky=downstream ! "
            "appsink name=asink max-buffers=2 drop=true sync=false")
            .arg(location, scaleCaps);

    GError* error = nullptr;
    gstPipeline = gst_parse_launch(description.toUtf8().constData(), &error);
    if (!gstPipeline) {
        qWarning() << "VideoView::gstPlay: pipeline build failed:"
                   << (error ? error->message : "unknown");
        if (error) {
            g_error_free(error);
        }
        return false;
    }
    if (error) {
        // Non-fatal parse warnings still populate error.
        g_error_free(error);
    }

    gstAppSink = gst_bin_get_by_name(GST_BIN(gstPipeline), "asink");
    if (!gstAppSink) {
        // Without the appsink no frames ever reach Qt — the pipeline would
        // go PLAYING to a silent black screen. Fail hard so the caller
        // logs it and the supervisor respawns, rather than limping.
        qWarning() << "VideoView::gstPlay: appsink 'asink' missing —"
                   << "tearing down";
        gstStop();
        return false;
    }
    GstAppSinkCallbacks callbacks = {};
    callbacks.new_sample = anthias_gst_on_new_sample;
    gst_app_sink_set_callbacks(
        GST_APP_SINK(gstAppSink), &callbacks, this, nullptr);

    // Bus watch for EOS (loop) and ERROR (tear down) — same handling as the
    // overlay path, so an appsink pipeline error is surfaced/recovered
    // instead of silently hanging. EOS looping moves here from the appsink
    // callback so there's a single loop path.
    GstBus* bus = gst_element_get_bus(gstPipeline);
    gst_bus_add_watch(bus, anthias_gst_bus_loop, this);
    gst_object_unref(bus);

    playStartedAt.start();
    if (gst_element_set_state(gstPipeline, GST_STATE_PLAYING)
        == GST_STATE_CHANGE_FAILURE) {
        qWarning() << "VideoView::gstPlay: could not start pipeline for"
                   << uri;
        gstStop();
        return false;
    }
    // appsink-raster fallback is up; give it audio too.
    gstStartAudio(localPath, alsaDev);
    return true;
}

bool VideoView::gstPlayOverlay(const QString& uri)
{
    QPlatformNativeInterface* ni = QGuiApplication::platformNativeInterface();
    if (!ni) {
        return false;
    }
    QScreen* screen = QGuiApplication::primaryScreen();
    if (!screen) {
        qWarning() << "VideoView::gstPlayOverlay: no primary screen —"
                   << "using raster path";
        return false;
    }
    const int driFd = static_cast<int>(reinterpret_cast<qintptr>(
        ni->nativeResourceForIntegration(QByteArrayLiteral("dri_fd"))));
    const quint32 crtcId = static_cast<quint32>(reinterpret_cast<qintptr>(
        ni->nativeResourceForScreen(QByteArrayLiteral("dri_crtcid"), screen)));
    const quint32 connId = static_cast<quint32>(reinterpret_cast<qintptr>(
        ni->nativeResourceForScreen(
            QByteArrayLiteral("dri_connectorid"), screen)));
    // driFd is eglfs's DRM master fd read back through the void*
    // nativeResource API: a real fd here is always >2 (0/1/2 are the
    // process's std streams, open before eglfs), and "no fd" comes back as
    // a null pointer → 0. So ``<= 0`` is the correct not-available test in
    // this context, not a rejection of a legitimately-0 fd.
    if (driFd <= 0 || crtcId == 0 || connId == 0) {
        qWarning() << "VideoView::gstPlayOverlay: eglfs DRM resources"
                   << "unavailable (fd" << driFd << "crtc" << crtcId
                   << "connector" << connId << ") — using raster path";
        return false;
    }

    const uint32_t planeId = anthias_find_overlay_plane(driFd, crtcId);
    if (planeId == 0) {
        qWarning() << "VideoView::gstPlayOverlay: no free overlay plane on"
                   << "crtc" << crtcId << "— using raster path";
        return false;
    }

    // Explicit HW pipeline (reliably reaches the overlay — the playbin
    // variant hung in set_state). pi3-64 only receives H.264/MP4 (the
    // codec gate rejects other codecs), so qtdemux ! h264parse !
    // v4l2h264dec forces the bcm2835 HW decoder. Default is decoder-direct
    // (I420 straight to kmssink — see the decode/convert stage notes
    // below); the ``queue`` decouples the decoder thread from the scanout
    // thread.
    QString location = uri;
    location.replace(QLatin1Char('\\'), QLatin1String("\\\\"));
    location.replace(QLatin1Char('"'), QLatin1String("\\\""));
    // Clamp to a sane positive count: a 0 / negative / non-numeric
    // ANTHIAS_GST_QUEUE would make queueBuffers 0, i.e. queue
    // max-size-buffers=0 with time/bytes also 0 = UNBOUNDED buffering →
    // OOM on the 1 GB board. Fall back to the default 4 in that case.
    int queueBuffers =
        qEnvironmentVariableIsSet("ANTHIAS_GST_QUEUE")
            ? qEnvironmentVariableIntValue("ANTHIAS_GST_QUEUE")
            : 4;
    if (queueBuffers <= 0) {
        queueBuffers = 4;
    }
    // io-mode of the ISP convert used ONLY by the fallback path
    // (ANTHIAS_GST_ISP_CONVERT=1). mmap makes that convert output
    // CPU-memory buffers so kmssink copies + releases them instead of
    // pinning its pool — the same copy-not-pin trick the decoder-direct
    // default applies to the decoder itself (see decodeStage). Overridable.
    const QString convIoMode =
        qEnvironmentVariable("ANTHIAS_GST_CONVERT_IOMODE",
                             QStringLiteral("mmap"));
    // Default (ANTHIAS_GST_ISP_CONVERT unset/0): decoder-direct — the
    // bcm2835 decoder emits I420, which the vc4 overlay plane scans out
    // directly, at full rate. The load-bearing bit is the DECODER's output
    // io-mode: with exported-dmabuf kmssink zero-copies and PINS the
    // decoder's DPB buffers on the plane, starving the pool → hard
    // deadlock; ``capture-io-mode=mmap`` makes the decoder output
    // CPU-memory buffers kmssink can't scan out directly, so it COPIES each
    // frame (~I420 1080p ≈ 3 MB, cheap) and releases the buffer
    // immediately — as the legacy fbdevsink did — giving stable 30 fps
    // with zero ongoing drops. ANTHIAS_GST_ISP_CONVERT=1 inserts the ISP
    // to emit NV12 instead (a second M2M pass, ~18 fps) for streams where
    // direct I420 scanout misbehaves.
    const bool useConvert =
        qEnvironmentVariableIsSet("ANTHIAS_GST_ISP_CONVERT")
            ? qEnvironmentVariableIntValue("ANTHIAS_GST_ISP_CONVERT") != 0
            : false;
    const QString decodeStage =
        useConvert
            ? QStringLiteral("v4l2h264dec")
            : QStringLiteral("v4l2h264dec capture-io-mode=mmap");
    const QString convertStage =
        !useConvert ? QString()
        : convIoMode.isEmpty()
            ? QStringLiteral("v4l2convert ! video/x-raw,format=NV12 ! ")
            : QStringLiteral(
                  "v4l2convert capture-io-mode=%1 ! video/x-raw,format=NV12 ! ")
                  .arg(convIoMode);
    // Audio is a SEPARATE GStreamer pipeline started by the caller
    // (gstPlay → gstStartAudio), NOT a branch off this video qtdemux: a
    // shared pipeline coupled the two fatally (a slow/queuing decodebin
    // preroll, an audio-provided clock that stalls, or a missing track
    // froze the VIDEO at the first frame). Keeping it independent means
    // this overlay chain stays byte-for-byte the proven 30 fps path and
    // can never be stalled by audio.
    const QString description =
        QStringLiteral(
            "filesrc location=\"%1\" ! qtdemux ! h264parse ! %7 ! "
            "queue max-size-buffers=%5 max-size-time=0 max-size-bytes=0 ! "
            "%6"
            "kmssink name=vsink qos=true fd=%2 connector-id=%3 plane-id=%4 "
            "force-modesetting=false can-scale=true skip-vsync=true")
            .arg(location)
            .arg(driFd)
            .arg(connId)
            .arg(planeId)
            .arg(queueBuffers)
            .arg(convertStage, decodeStage);

    GError* error = nullptr;
    gstPipeline = gst_parse_launch(description.toUtf8().constData(), &error);
    if (!gstPipeline) {
        qWarning() << "VideoView::gstPlayOverlay: pipeline build failed:"
                   << (error ? error->message : "unknown")
                   << "— using raster path";
        if (error) {
            g_error_free(error);
        }
        return false;
    }
    if (error) {
        g_error_free(error);
        error = nullptr;
    }

    // Count frames reaching kmssink (presentation rate) + latch the source
    // fps for sampleStats. (gstRawSamples is reset once in gstPlay for both
    // paths.)
    setContainerFps(0.0);
    GstElement* sink = gst_bin_get_by_name(GST_BIN(gstPipeline), "vsink");
    if (sink) {
        GstPad* sinkPad = gst_element_get_static_pad(sink, "sink");
        if (sinkPad) {
            gst_pad_add_probe(sinkPad, GST_PAD_PROBE_TYPE_BUFFER,
                              anthias_gst_kms_probe, this, nullptr);
            gst_object_unref(sinkPad);
        }
        gst_object_unref(sink);
    }

    // Loop the clip: kmssink has no appsink EOS callback, so watch the bus
    // (serviced on the GUI thread by Qt's GLib dispatcher on Linux).
    GstBus* bus = gst_element_get_bus(gstPipeline);
    gst_bus_add_watch(bus, anthias_gst_bus_loop, this);
    gst_object_unref(bus);

    playStartedAt.start();
    if (gst_element_set_state(gstPipeline, GST_STATE_PLAYING)
        == GST_STATE_CHANGE_FAILURE) {
        qWarning() << "VideoView::gstPlayOverlay: could not start pipeline"
                   << "for" << uri << "— using raster path";
        gstStop();
        return false;
    }

    // The video lives on the overlay plane now; clear the raster widget so
    // the eglfs primary plane behind any letterbox bars is black, not a
    // stale frame.
    if (rasterWidget) {
        rasterWidget->clear();
    }
    gstOverlayActive = true;
    writeStats(
        QStringLiteral("INIT"),
        QStringLiteral(
            "backend=gstreamer/v4l2 sink=kms-overlay plane-id=%1 qt=%2")
            .arg(planeId)
            .arg(QStringLiteral(QT_VERSION_STR)));
    // Audio is started by the caller (gstPlay) so it also covers the
    // appsink-raster fallback, not just this overlay path.
    return true;
}

void VideoView::gstStartAudio(const QString& location, const QString& alsaDev)
{
    const QString audioSink =
        qEnvironmentVariable("ANTHIAS_GST_AUDIO_SINK",
                             QStringLiteral("alsasink"));
    // Escape the device string too (same as the filesrc path): it's
    // interpolated into the gst_parse_launch description, so a quote or
    // backslash would break the pipeline parse.
    QString devEsc = alsaDev;
    devEsc.replace(QLatin1Char('\\'), QLatin1String("\\\\"));
    devEsc.replace(QLatin1Char('"'), QLatin1String("\\\""));
    const QString audioDeviceProp =
        (audioSink == QLatin1String("alsasink") && !alsaDev.isEmpty())
            ? QStringLiteral(" device=\"%1\"").arg(devEsc)
            : QString();
    // Escape the path for the filesrc string (caller passes a raw path).
    QString loc = location;
    loc.replace(QLatin1Char('\\'), QLatin1String("\\\\"));
    loc.replace(QLatin1Char('"'), QLatin1String("\\\""));
    // decodebin autoplugs the audio codec (AAC/MP3/…); the sink drives the
    // HDMI ALSA card directly. Own pipeline → own clock/preroll, fully
    // isolated from the video overlay pipeline.
    // Pin the AUDIO pad explicitly: qtdemux exposes both a video and an
    // audio pad, and a bare ``qtdemux ! decodebin`` links whichever appears
    // first (often video → audioconvert then can't negotiate and no sound).
    const QString audioDesc =
        QStringLiteral(
            "filesrc location=\"%1\" ! qtdemux name=ademux ademux.audio_0 ! "
            "queue ! decodebin ! audioconvert ! audioresample ! %2%3")
            .arg(loc, audioSink, audioDeviceProp);

    GError* error = nullptr;
    gstAudioPipeline =
        gst_parse_launch(audioDesc.toUtf8().constData(), &error);
    if (!gstAudioPipeline) {
        writeStats(QStringLiteral("AUDIO"),
                   QStringLiteral("build-failed err=%1")
                       .arg(error ? error->message : QStringLiteral("?")));
        if (error) {
            g_error_free(error);
        }
        return;
    }
    if (error) {
        g_error_free(error);
    }
    GstBus* bus = gst_element_get_bus(gstAudioPipeline);
    gst_bus_add_watch(bus, anthias_gst_audio_bus, this);
    gst_object_unref(bus);
    if (gst_element_set_state(gstAudioPipeline, GST_STATE_PLAYING)
        == GST_STATE_CHANGE_FAILURE) {
        // Best-effort audio: tear the failed pipeline down and leave the
        // video playing silently rather than keeping a dead pipeline.
        writeStats(QStringLiteral("AUDIO"),
                   QStringLiteral("start-failed sink=%1 device=%2")
                       .arg(audioSink,
                            alsaDev.isEmpty() ? QStringLiteral("(default)")
                                              : alsaDev));
        gstStopAudio();
        return;
    }
    writeStats(QStringLiteral("AUDIO"),
               QStringLiteral("started sink=%1 device=%2")
                   .arg(audioSink,
                        alsaDev.isEmpty() ? QStringLiteral("(default)")
                                          : alsaDev));
}

void VideoView::gstLoopAudio()
{
    if (!gstAudioPipeline) {
        return;
    }
    gst_element_seek_simple(
        GST_ELEMENT(gstAudioPipeline), GST_FORMAT_TIME,
        static_cast<GstSeekFlags>(
            GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT),
        0);
}

void VideoView::logAudio(const QString& msg)
{
    writeStats(QStringLiteral("AUDIO"), msg);
}

void VideoView::latchSourceFps(int fpsNum, int fpsDen)
{
    // Latch once (the overlay pad-probe latches the same way); ignore a
    // degenerate 0/0 or variable-rate caps.
    if (fpsDen > 0 && fpsNum > 0 && containerFps() <= 0.0) {
        setContainerFps(static_cast<qreal>(fpsNum) / fpsDen);
    }
}

void VideoView::gstStopAudio()
{
    if (!gstAudioPipeline) {
        return;
    }
    GstBus* bus = gst_element_get_bus(gstAudioPipeline);
    if (bus) {
        gst_bus_remove_watch(bus);
        gst_object_unref(bus);
    }
    gst_element_set_state(gstAudioPipeline, GST_STATE_NULL);
    gst_object_unref(gstAudioPipeline);
    gstAudioPipeline = nullptr;
}

void VideoView::gstStop()
{
    gstStopAudio();
    if (gstPipeline) {
        // Remove the bus watch (both the overlay and the appsink paths
        // install one; a no-op if it was already dropped, e.g. after an
        // error watch returned FALSE).
        GstBus* bus = gst_element_get_bus(gstPipeline);
        if (bus) {
            gst_bus_remove_watch(bus);
            gst_object_unref(bus);
        }
    }
    gstOverlayActive = false;
    if (gstAppSink) {
        gst_object_unref(gstAppSink);
        gstAppSink = nullptr;
    }
    if (gstPipeline) {
        gst_element_set_state(gstPipeline, GST_STATE_NULL);
        gst_object_unref(gstPipeline);
        gstPipeline = nullptr;
    }
    // A drain (onGstFrame) may already be queued on the GUI thread from a
    // late pushGstFrame. Drop the stashed frame so that queued drain is a
    // no-op (it early-returns on a null image) instead of repainting a
    // stale frame after teardown; reset the coalescing flag too.
    {
        QMutexLocker locker(&gstFrameMutex);
        gstLatestFrame = QImage();
    }
    gstDrainPending.storeRelaxed(0);
}

void VideoView::pushGstFrame(const QImage& frame)
{
    // Streaming thread: stash the newest frame and post at most one
    // coalesced drain to the GUI thread.
    gstRawSamples.fetchAndAddRelaxed(1);
    {
        QMutexLocker locker(&gstFrameMutex);
        gstLatestFrame = frame;
    }
    if (gstDrainPending.testAndSetOrdered(0, 1)) {
        QMetaObject::invokeMethod(this, "onGstFrame", Qt::QueuedConnection);
    }
}

void VideoView::onGstFrame()
{
    // GUI thread: take the freshest stashed frame and blit it. The paint
    // that follows setImage() bumps framesRendered in onRasterPainted(),
    // so SAMPLE reports the true present rate; gstRawSamples (logged in
    // sampleStats) reports the pipeline's own delivery rate for contrast.
    QImage frame;
    {
        QMutexLocker locker(&gstFrameMutex);
        frame = gstLatestFrame;
    }
    if (!frame.isNull()) {
        ++framesDelivered;
        ++framesForwarded;
        if (rasterWidget) {
            rasterWidget->setImage(frame);
        }
    }
    // Clear the coalescing flag at the END: while this drain ran,
    // pushGstFrame kept gstDrainPending==1 (testAndSetOrdered) so no extra
    // onGstFrame was ever queued behind it — frames that arrived meanwhile
    // just updated gstLatestFrame. storeRelease pairs with the acquire in
    // pushGstFrame's testAndSetOrdered so this drain's writes land before
    // the next drain can be queued, keeping the GUI event queue bounded to
    // a single in-flight drain.
    gstDrainPending.storeRelease(0);
}

void VideoView::gstRestartLoop()
{
    if (!gstPipeline) {
        return;
    }
    gst_element_seek_simple(
        GST_ELEMENT(gstPipeline), GST_FORMAT_TIME,
        static_cast<GstSeekFlags>(
            GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT),
        0);
    // Re-seek audio to the top too so it re-aligns with the video every
    // loop (the two pipelines otherwise drift on their own clocks).
    gstLoopAudio();
}

void VideoView::onOverlayBuffer(struct _GstPad* pad)
{
    gstRawSamples.fetchAndAddRelaxed(1);
    if (containerFps() <= 0.0 && pad) {
        GstCaps* caps = gst_pad_get_current_caps(pad);
        if (caps) {
            GstStructure* s = gst_caps_get_structure(caps, 0);
            gint num = 0;
            gint den = 0;
            if (s && gst_structure_get_fraction(s, "framerate", &num, &den)
                && den > 0) {
                setContainerFps(static_cast<qreal>(num) / den);
            }
            gst_caps_unref(caps);
        }
    }
}

void VideoView::logOverlayQos(quint64 rendered, quint64 dropped)
{
    writeStats(
        QStringLiteral("QOS"),
        QStringLiteral("sink-rendered=%1 sink-dropped=%2")
            .arg(rendered)
            .arg(dropped));
}

#endif  // ANTHIAS_GSTREAMER
