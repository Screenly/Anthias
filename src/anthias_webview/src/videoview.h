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
class QGraphicsScene;
class QGraphicsVideoItem;
class QGraphicsView;
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
// QGraphicsView + QGraphicsScene + QGraphicsVideoItem is the
// rendering substrate (not QVideoWidget). The graphics-item path
// is what lets ``video-rotate`` actually rotate the displayed
// frames — ``QGraphicsItem::setRotation`` is honoured by the
// painter, whereas QVideoWidget has no rotation property and a
// dynamic property attached via ``setProperty`` is read by
// nothing (review of PR #2905 caught this as a silent Pi 4
// regression for any operator whose Settings page screen_rotation
// was non-zero). All three boards run the same path so the
// transformation is testable in CI; the cage boards still inherit
// rotation from wlr-randr at the compositor level and so ignore
// the option dict's ``video-rotate`` value.
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
    //   * ``video-rotate`` — int as string (0/90/180/270), Pi 4 only
    //     (cage / wayland boards inherit rotation from wlr-randr).
    //     Applied to the QGraphicsVideoItem so the painter rotates
    //     the visible frames; the rotated bounding box is mapped
    //     back to the viewport so a 90° rotation of 1920x1080 fits
    //     inside the 1080-tall surface without clipping.
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

protected:
    void resizeEvent(QResizeEvent* event) override;

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
    // ``videoFrameChanged`` fires per display tick on the
    // QGraphicsVideoItem's underlying sink; counting those is the
    // most direct measurement QtMultimedia exposes.
    void onVideoFrameDelivered();

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
    // Apply (or clear) the rotation on the QGraphicsVideoItem and
    // resize the viewport so the rotated frame fills the surface
    // without clipping. ``angle`` is normalised to {0, 90, 180, 270};
    // anything else snaps to 0.
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

    // Rotation is applied around the centre so 90/270 swap the
    // bounding box around the same on-screen midpoint as 0/180.
    void positionVideoItem();

    QMediaPlayer* player = nullptr;
    QAudioOutput* audioOutput = nullptr;
    QGraphicsView* graphicsView = nullptr;
    QGraphicsScene* graphicsScene = nullptr;
    QGraphicsVideoItem* videoItem = nullptr;
    QHBoxLayout* videoLayout = nullptr;
    int currentRotation = 0;

    // Stats state. Same shape as the libmpv version so log
    // consumers don't have to change schemas. ``playStartedAt`` is
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
    qreal containerFps = 0.0;

    // Cap on /data/.anthias/playback-stats.log size. 8 MB ≈ a full
    // 24 h burn-in's worth of SAMPLE lines at 1 Hz; past that we
    // truncate on the next viewer start so a long-running device
    // doesn't fill its 15 GB SD card with stats. The log is
    // best-effort instrumentation, not durable history.
    static constexpr qint64 kMaxStatsLogBytes = 8 * 1024 * 1024;
};
