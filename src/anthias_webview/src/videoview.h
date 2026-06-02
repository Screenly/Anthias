#pragma once

#include <QAudioDevice>
#include <QElapsedTimer>
#include <QFile>
#include <QHBoxLayout>
#include <QMediaPlayer>
#include <QString>
#include <QTextStream>
#include <QTimer>
#include <QUrl>
#include <QVariantMap>
#include <QWidget>

class QAudioOutput;
class QQuickItem;
class QQuickWidget;
class QVideoSink;

// VideoView owns the Qt 6 multimedia playback pipeline for the Qt6
// boards (issue #2904). An earlier revision embedded libmpv via
// ``mpv_render_context`` into a ``QOpenGLWidget`` to eliminate the
// two-process DRM-master contention #2885 documented, and confirmed
// every HW decoder engaged — but real-device measurement on Pi 4
// left frame drops in the same 600-2973 / 60 s range as the
// subprocess baseline. The bottleneck is libmpv-render's chain of
// GL upload → ``QOpenGLWidget`` FBO → Qt compositor blit; V3D 6.0's
// fillrate can't sustain 60 fps through that. A ``QOpenGLWindow``
// direct-swap workaround crashed because eglfs is
// single-native-window-per-process.
//
// QtMultimedia is the shipping path. Qt 6.5 dropped the upstream
// gstreamer media backend, so Debian Trixie ships only the
// ffmpeg-backed ``libffmpegmediaplugin.so``; decode runs through
// libavcodec directly. The +rpt1 ``libavcodec`` packages pinned in
// ``docker/_rpt1-ffmpeg-pin.j2`` carry ``--enable-v4l2-request`` /
// ``--enable-v4l2-m2m``, so libavcodec engages the same hardware
// decoders libmpv era used (rpi-hevc-dec, bcm2835-codec, Hantro G2
// on Pi 5, rkvdec on Rock Pi 4) without any per-codec dispatch
// from the application.
//
// The rendering substrate is a QML ``VideoOutput`` hosted in a
// ``QQuickWidget`` (issue #2967). The previous substrate —
// ``QGraphicsVideoItem`` on a default (raster) ``QGraphicsView``
// viewport — hardware-decoded perfectly but presented at 8–12 fps:
// every frame went ``QVideoFrame::toImage()`` → ``qImageFromVideoFrame``
// (an RHI offscreen render **plus GPU→CPU readback**), then a
// smooth-scaled CPU raster blit into the widget backing store,
// which eglfs/wayland re-uploaded to the GPU to composite. Two
// GPU/CPU crossings per frame saturated the GUI thread (~80% on
// Pi 4) while ``playback-stats.log`` — which counts sink
// deliveries, not paints — still read "dropped≈0". VideoOutput's
// frames stay on the GPU: the scene graph samples the decoded
// planes as textures and converts YUV→RGB in a fragment shader at
// composite time. QQuickWidget renders through QQuickRenderControl
// into an FBO inside the app's single native window — the same
// machinery QWebEngineView already uses here, so it is proven on
// eglfs (single-native-window constraint) and proven to inherit
// QT_QPA_EGLFS_ROTATION / wlr-randr whole-screen rotation (#2971).
// A ``QVideoWidget`` would NOT satisfy the eglfs constraint: it
// wraps a ``QVideoWindow`` — a second native window.
//
// The MainWindow D-Bus surface (``playVideo`` / ``stopVideo`` /
// ``videoEnded``) and the Python option-dict contract are
// unchanged — clients see the same interface even though the
// underlying playback engine swapped.
class VideoView : public QWidget
{
    Q_OBJECT

public:
    explicit VideoView(QWidget* parent = nullptr);
    ~VideoView() override;

    // Apply per-file options then hand the URI to QMediaPlayer.
    // ``options`` keys:
    //
    //   * ``audio-device`` — ALSA device name (the same string the
    //     mpv era used; QAudioDevice consumes the ``CARD=<name>``
    //     portion).
    //   * ``video-rotate`` — int as string (0/90/180/270). Defensive
    //     no-op: no board sends it any more (every platform rotates
    //     the whole screen at the compositor / QPA layer). Applied
    //     to the VideoOutput item's ``orientation`` property so an
    //     old caller still gets rotated frames instead of an error.
    //
    // ``hwdec`` / ``vd-lavc-threads`` / ``video-sync`` from the
    // libmpv option set are deliberately ignored — libavcodec
    // engages the v4l2_request / v4l2_m2m decoders automatically
    // and handles sync internally.
    void play(const QString& uri, const QVariantMap& options);

    // Stops the current file. QMediaPlayer's state stays alive so
    // the next ``play()`` is a cheap setSource + play, not a
    // pipeline rebuild.
    void stop();

signals:
    // Fires on ``QMediaPlayer::EndOfMedia``. Re-emitted by
    // MainWindow as a D-Bus signal so Python can drop the
    // ``time.sleep(duration)`` poll in a follow-up — not subscribed
    // yet.
    void videoEnded();

private slots:
    void onPlaybackStateChanged(QMediaPlayer::PlaybackState state);
    void onMediaStatusChanged(QMediaPlayer::MediaStatus status);
    void onErrorOccurred(QMediaPlayer::Error error, const QString& message);

    // 1 Hz sampler. Writes the current position / duration / a
    // rolling estimate of dropped frames to the stats log while a
    // file is playing.
    void sampleStats();

    // Counts frames delivered to QVideoSink so the SAMPLE / END_FILE
    // log lines can compare ``actually displayed`` against
    // ``decoder-expected`` and report a dropped-frame estimate.
    // ``videoFrameChanged`` fires once per frame the pipeline hands
    // to the sink — i.e. the *decode-side* rate.
    void onVideoFrameDelivered();

    // Counts scene-graph render passes
    // (``QQuickWindow::afterRendering``). The Quick scene only
    // re-renders on damage, and during video playback the
    // VideoOutput's frame updates are the damage — so this is the
    // *presentation-side* rate. Issue #2967 existed precisely
    // because the stats log had no such counter: sink deliveries
    // read "dropped≈0" while ~70% of frames never reached the
    // screen. SAMPLE / END_FILE now log both ends of the pipe.
    void onSceneRendered();

private:
    // Wire onSceneRendered to the VideoOutput item's QQuickWindow.
    // Idempotent; called from the constructor and re-tried from
    // play() so a hypothetical late item→window attachment can't
    // leave the presentation counter permanently at 0 (which would
    // read as a total presentation failure — the inverse of the
    // #2967 blind spot).
    void connectRenderCounter();

private:
    // Resolve an ALSA device name (``alsa/sysdefault:CARD=vc4hdmi0``,
    // produced by ``anthias_viewer.media_player.get_alsa_audio_device``)
    // to a ``QAudioDevice`` from the system list. The Python side
    // passes a full ALSA spec like ``sysdefault:CARD=<name>``; the
    // ``CARD=<name>`` segment is the discriminator. Falls back to the
    // default audio output when no card matches so a typo doesn't
    // silence playback. Logs the chosen device id at INFO so the
    // resolved card is visible in journalctl (review of #2905 flagged
    // that a substring-only matcher was unreliable on multi-HDMI
    // boards; this routine extracts ``CARD=`` and matches that
    // segment specifically).
    QAudioDevice resolveAlsaDevice(const QString& alsaSpec) const;
    // Apply (or clear) the rotation on the VideoOutput item's
    // ``orientation`` property. ``angle`` is normalised to
    // {0, 90, 180, 270}; anything else snaps to 0. VideoOutput
    // handles the 90/270 bounding-box swap itself (the property
    // exists for camera-orientation use), so there is no manual
    // transform-origin / transpose bookkeeping here.
    void applyRotation(int angle);
    // Append ``ISO-8601 KIND detail`` to
    // ``/data/.anthias/playback-stats.log``. Renamed from the
    // libmpv-era ``mpv-stats.log`` now that the player is
    // QtMultimedia + libavcodec — keeping the old name was
    // misleading to anyone tailing the log without project context.
    // If the file is past ``kMaxStatsLogBytes`` at INIT time it is
    // truncated — the log is best-effort observability, not durable
    // history.
    void writeStats(const QString& kind, const QString& detail);
    // Open / re-open the stats log, truncating if it has grown past
    // the cap. Called from the constructor on viewer start; not
    // called per-line — the cost-per-line stays an append + flush.
    void openStatsLog();

    QMediaPlayer* player = nullptr;
    QAudioOutput* audioOutput = nullptr;
    QQuickWidget* quickWidget = nullptr;
    // The QML VideoOutput item (owned by the QQuickWidget's root
    // object) and its sink. Both are guaranteed non-null past the
    // constructor: a failed QML load (missing qml6-module-* runtime
    // packages) is a qFatal there, because decode-but-render-nowhere
    // is a silent black screen on a kiosk while crash-respawn is
    // loud and supervised.
    QQuickItem* videoOutputItem = nullptr;
    QVideoSink* videoSink = nullptr;
    QHBoxLayout* videoLayout = nullptr;
    QMetaObject::Connection renderCounterConnection;
    int currentRotation = 0;

    // Stats state. Extends the libmpv-era line shape with a
    // ``frames-rendered=`` field (the presentation-side counter
    // #2967 was missing); all fields are key=value tagged, so
    // consumers must key on field names, not positions —
    // positional parsers of STOP/SAMPLE/END_FILE lines break on
    // this revision. ``playStartedAt`` is
    // restarted on ``LoadedMedia`` (not in play()) so the elapsed
    // window measures real playback wall-clock, not decoder init —
    // review of #2905 flagged that the init delay inflated drop
    // counts by ~5-10 frames per first-clip.
    QFile* statsFile = nullptr;
    QTextStream* statsStream = nullptr;
    QTimer* statsTimer = nullptr;
    QString currentUri;
    QElapsedTimer playStartedAt;
    qint64 framesDelivered = 0;
    qint64 framesRendered = 0;
    qreal containerFps = 0.0;

    // Cap on /data/.anthias/playback-stats.log size. 8 MB ≈ a full
    // 24 h burn-in's worth of SAMPLE lines at 1 Hz; past that we
    // truncate on the next viewer start so a long-running device
    // doesn't fill its 15 GB SD card with stats. The log is
    // best-effort instrumentation, not durable history.
    static constexpr qint64 kMaxStatsLogBytes = 8 * 1024 * 1024;
};
