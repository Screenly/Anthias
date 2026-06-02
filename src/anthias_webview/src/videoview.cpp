#include "videoview.h"

#include <QAudioOutput>
#include <QDateTime>
#include <QDebug>
#include <QDir>
#include <QFileInfo>
#include <QGraphicsScene>
#include <QGraphicsVideoItem>
#include <QGraphicsView>
#include <QMediaDevices>
#include <QMediaMetaData>
#include <QRegularExpression>
#include <QSizeF>
#include <QVariant>
#include <QVideoFrame>
#include <QVideoSink>
#include <QtGlobal>


VideoView::VideoView(QWidget* parent) : QWidget(parent)
{
    // Black background so the surface doesn't flash white at the
    // start of playback while libavcodec's V4L2 decoder negotiates
    // its first capture buffer.
    setAutoFillBackground(true);
    QPalette pal = palette();
    pal.setColor(QPalette::Window, Qt::black);
    setPalette(pal);

    videoLayout = new QHBoxLayout(this);
    videoLayout->setContentsMargins(0, 0, 0, 0);
    videoLayout->setSpacing(0);

    graphicsScene = new QGraphicsScene(this);
    graphicsScene->setBackgroundBrush(Qt::black);
    graphicsView = new QGraphicsView(graphicsScene, this);
    graphicsView->setFrameShape(QFrame::NoFrame);
    graphicsView->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    graphicsView->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    graphicsView->setRenderHint(QPainter::SmoothPixmapTransform);
    graphicsView->setAlignment(Qt::AlignCenter);
    videoLayout->addWidget(graphicsView);

    videoItem = new QGraphicsVideoItem;
    // Aspect-ratio behaviour: keep the source ratio so a 16:9 clip
    // on a 16:9 display fills the surface without stretching.
    videoItem->setAspectRatioMode(Qt::KeepAspectRatio);
    graphicsScene->addItem(videoItem);

    player = new QMediaPlayer(this);
    audioOutput = new QAudioOutput(this);
    player->setAudioOutput(audioOutput);
    player->setVideoOutput(videoItem);

    connect(player, &QMediaPlayer::playbackStateChanged,
            this, &VideoView::onPlaybackStateChanged);
    connect(player, &QMediaPlayer::mediaStatusChanged,
            this, &VideoView::onMediaStatusChanged);
    connect(player, &QMediaPlayer::errorOccurred,
            this, &VideoView::onErrorOccurred);

    // Count frames delivered to the sink so SAMPLE / END_FILE lines
    // can report a "frames delivered" → "frames expected" delta.
    // QVideoSink::videoFrameChanged fires once per displayed frame
    // (after libavcodec / V4L2 drops happen upstream), which is the
    // metric we care about — frames that reached the display.
    if (videoItem->videoSink()) {
        connect(videoItem->videoSink(), &QVideoSink::videoFrameChanged,
                this, &VideoView::onVideoFrameDelivered);
    }

    openStatsLog();

    statsTimer = new QTimer(this);
    statsTimer->setInterval(1000);
    connect(statsTimer, &QTimer::timeout, this, &VideoView::sampleStats);

    positionVideoItem();
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
                "backend=qtmultimedia/ffmpeg qt=%1 "
                "audio_default=%2")
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

    // Optional per-item rotation of the QGraphicsVideoItem. No board
    // sends ``video-rotate`` any more: every platform now rotates the
    // whole screen (eglfs via QT_QPA_EGLFS_ROTATION on Pi 4, wlroots
    // via wlr-randr on x86) and the video item inherits that transform,
    // so applying it again here would double-rotate. The parse is kept
    // as a defensive no-op (default 0 = applyRotation(0)) so an old
    // viewer that still passes the option degrades gracefully rather
    // than erroring on an unknown key.
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

    currentUri = uri;
    // playStartedAt is RESTARTED on LoadedMedia (not here) so the
    // elapsed-ms window measures real playback time, not decoder
    // init. Reset framesDelivered now so the very first frame
    // count is clean.
    playStartedAt.invalidate();
    framesDelivered = 0;
    containerFps = 0.0;
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
                "position-ms=%4")
                .arg(currentUri)
                .arg(elapsedMs)
                .arg(framesDelivered)
                .arg(player->position()));
    }
    player->stop();
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
        // dropped on the way to the display, the same number mpv
        // exposed as ``frame-drop-count``. Reported with the raw
        // delivered count so the consumer can recompute if they
        // disagree with the fps source.
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
                "expected=%4 dropped=%5")
                .arg(currentUri)
                .arg(elapsedMs)
                .arg(framesDelivered)
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
            "position-ms=%1 frames-delivered=%2 expected=%3 dropped=%4")
            .arg(posMs)
            .arg(framesDelivered)
            .arg(qRound(expected))
            .arg(dropped));
}

void VideoView::onVideoFrameDelivered()
{
    ++framesDelivered;
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
    if (!videoItem) {
        return;
    }
    // ``QGraphicsItem::setRotation`` rotates around the item's
    // transformOrigin (we set this in positionVideoItem). Unlike
    // the prior ``QVideoWidget::setProperty("rotation", …)`` this
    // is actually consumed by the painter, so the operator sees
    // a rotated video instead of an unrotated one.
    videoItem->setRotation(currentRotation);
    positionVideoItem();
}

void VideoView::positionVideoItem()
{
    if (!videoItem || !graphicsView || !graphicsScene) {
        return;
    }
    const QSizeF viewport(graphicsView->viewport()->width(),
                          graphicsView->viewport()->height());
    if (viewport.isEmpty()) {
        return;
    }
    // 90/270 rotations swap the bounding box, so the item's native
    // size becomes the viewport's height × width (rotated). 0/180
    // use the viewport directly. The QGraphicsVideoItem then maps
    // the source frame into this size, honouring the
    // KeepAspectRatio mode.
    QSizeF itemSize = viewport;
    if (currentRotation == 90 || currentRotation == 270) {
        itemSize.transpose();
    }
    videoItem->setSize(itemSize);
    // Centre the item in the scene so setRotation rotates around
    // the visible mid-point.
    const QRectF bounds(QPointF(0, 0), itemSize);
    videoItem->setTransformOriginPoint(bounds.center());
    videoItem->setPos(
        (viewport.width() - itemSize.width()) / 2.0,
        (viewport.height() - itemSize.height()) / 2.0);
    graphicsScene->setSceneRect(0, 0, viewport.width(), viewport.height());
}

void VideoView::resizeEvent(QResizeEvent* event)
{
    QWidget::resizeEvent(event);
    positionVideoItem();
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
