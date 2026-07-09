#pragma once

#include <QAtomicInteger>
#include <QAudioDevice>
#include <QByteArray>
#include <QElapsedTimer>
#include <QFile>
#include <QImage>
#include <QMutex>
#include <QHBoxLayout>
#include <QMediaPlayer>
#include <QString>
#include <QTextStream>
#include <QTimer>
#include <QUrl>
#include <QVariantMap>
#include <QVideoFrame>
#include <QWidget>

class QAudioOutput;
class QQuickItem;
class QQuickWidget;
class QVideoSink;
class RasterVideoWidget;

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

    // Receives every frame the pipeline hands to ``pacingSink`` (the
    // *decode-side* rate, counted as ``frames-delivered``) and
    // forwards it to the QML VideoOutput's sink only when the scene
    // graph has composited the previously forwarded frame. Without
    // the gate, a 60 fps source schedules a scene render per
    // delivery on a GUI thread that sustains ~45 renders/s at 1080p
    // on a Pi 4 — the resulting overload presented 22.6 fps with the
    // playback position falling to ~0.6x realtime (issue #2987).
    // Dropping early self-paces delivery to render capacity: 30 fps
    // sources are untouched (render finishes well inside the frame
    // interval), 60 fps sources settle into an even ~half cadence.
    void onVideoFrameDelivered(const QVideoFrame& frame);

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

    // --- Pi 3 (VideoCore IV) CPU-raster presenter ------------------
    // pi3-64's GLES2-only GPU can't present the QML VideoOutput RHI
    // path: the scene renders but the QQuickWidget FBO never reaches
    // the eglfs scanout, so every video black-screens (issue #3084)
    // while images/webpages — which composite through the widget
    // backing store — render fine. When ANTHIAS_VIDEO_RASTER is set
    // (by the Python side for pi3-64 only, see _build_webview_env),
    // frames are presented instead by ``QVideoFrame::toImage()`` +
    // a backing-store blit — the same path images use, and the
    // pre-#2967 substrate that was visible (if slow, 8–12 fps) on
    // this GPU. rasterWidget replaces quickWidget in the layout;
    // videoOutputItem / videoSink stay null.
    //
    // presentRasterFrame converts + shows one frame and closes the
    // one-frame pacing gate; onRasterPainted (called by
    // RasterVideoWidget::paintEvent) re-opens it and forwards any
    // frame parked in pendingFrame while the paint was in flight.
    void presentRasterFrame(const QVideoFrame& frame);
    void onRasterPainted();
    friend class RasterVideoWidget;

#ifdef ANTHIAS_GSTREAMER
public:
    // GUI-thread drain slot: blits the newest frame the appsink has
    // stashed in gstLatestFrame. Coalesced — the appsink callback only
    // posts one invocation until this runs, so a GUI thread slower than
    // the pipeline drops intermediate frames (keeping the freshest)
    // instead of growing an unbounded event queue and OOM'ing the 1 GB
    // board. Q_INVOKABLE so the streaming-thread callback can
    // QMetaObject::invokeMethod it across the thread boundary.
    Q_INVOKABLE void onGstFrame();
    // Streaming-thread entry point: the appsink callback hands each
    // ISP-converted RGB frame here; it stashes the newest under
    // gstFrameMutex and posts a single coalesced onGstFrame() drain.
    void pushGstFrame(const QImage& frame);
    // Flushing-seek the pipeline to zero to loop the clip, on EOS. Called
    // from the pipeline bus watch (anthias_gst_bus_loop), which GLib
    // services on the GUI thread, so the seek never races stop().
    Q_INVOKABLE void gstRestartLoop();
    // Overlay-path instrumentation (streaming thread). onOverlayBuffer:
    // count each frame reaching kmssink + latch the source framerate from
    // the pad caps. logOverlayQos: record kmssink's authoritative
    // rendered/dropped tallies from a bus QoS message.
    void onOverlayBuffer(struct _GstPad* pad);
    void logOverlayQos(quint64 rendered, quint64 dropped);
    // Latch the source frame rate (once) from the appsink's negotiated
    // caps, so the appsink-raster path's SAMPLE can compute
    // expected/dropped instead of reporting -1. Called from the appsink
    // new-sample callback (streaming thread); setContainerFps is atomic.
    void latchSourceFps(int fpsNum, int fpsDen);
    // Best-effort audio in its OWN pipeline, decoupled from the video
    // overlay pipeline so no audio fault can stall the video. gstStartAudio
    // builds+plays it; gstLoopAudio/gstStopAudio are driven from the audio
    // bus watch (loop on EOS, tear down on error) and gstStop/gstRestart.
    void gstStartAudio(const QString& location, const QString& alsaDev);
    void gstLoopAudio();
    void gstStopAudio();
    // Record an AUDIO status/error line to the readable stats log (public
    // so the audio bus-watch free function can report errors there).
    void logAudio(const QString& msg);

private:
    // In-process GStreamer HW video path for pi3-64 (issue #3084):
    // filesrc ! qtdemux ! h264parse ! v4l2h264dec ! v4l2convert(ISP) !
    // RGB16 ! appsink. The bcm2835 ISP does the SAND→RGB conversion in
    // hardware — the CPU can't (~600 ms/frame, [[toImage]] wall) — so the
    // decode+convert stays fully on the VideoCore IV hardware and only a
    // ready-made RGB frame reaches Qt, which rasterWidget blits (~26 fps,
    // 0 drops measured). Active when rasterMode is built with
    // ANTHIAS_GSTREAMER; every other board keeps the VideoOutput path.
    bool gstPlay(const QString& uri, const QVariantMap& options);
    // HW overlay-plane path (ANTHIAS_VIDEO_OVERLAY): render decoded frames
    // straight onto a vc4 DRM overlay plane via kmssink, using eglfs's own
    // DRM master fd / CRTC / connector — the display controller composites
    // it with the eglfs UI plane in hardware, bypassing the GL compositor
    // (which caps at ~9 fps on VideoCore IV). Returns false (caller falls
    // back to the appsink→raster path) if the eglfs DRM resources or a
    // free overlay plane can't be resolved.
    bool gstPlayOverlay(const QString& uri);
    void gstStop();
    bool gstOverlayMode = false;
    bool gstOverlayActive = false;
    struct _GstElement* gstPipeline = nullptr;
    struct _GstElement* gstAudioPipeline = nullptr;
    struct _GstElement* gstAppSink = nullptr;
    bool gstMode = false;
    // Single-slot handoff: the appsink callback (streaming thread) writes
    // the newest frame under gstFrameMutex and, if no drain is already
    // queued (gstDrainPending), posts one onGstFrame() to the GUI thread.
    QImage gstLatestFrame;
    QMutex gstFrameMutex;
    QAtomicInt gstDrainPending{0};
    // Raw appsink delivery count (streaming thread) — logged alongside the
    // painted count so SAMPLE shows the pipeline's own rate vs what the
    // GUI actually presented.
    QAtomicInteger<qint64> gstRawSamples{0};
#endif

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

    // Raster path (ANTHIAS_VIDEO_RASTER). rasterWidget is non-null and
    // owns the on-screen surface only when rasterMode is true; it is
    // mutually exclusive with quickWidget/videoOutputItem/videoSink.
    RasterVideoWidget* rasterWidget = nullptr;
    bool rasterMode = false;
    // One-frame pacing gate for the raster path (the analogue of
    // sceneReadyForFrame for the VideoOutput path): true once the last
    // shown frame has been painted, so the next delivery converts and
    // shows immediately; otherwise the freshest frame parks in
    // pendingFrame until onRasterPainted() forwards it. Starts true so
    // the first frame always shows.
    bool rasterReady = true;
    // Raster-path instrumentation: total QVideoFrame::toImage() time
    // (µs) and count since the last SAMPLE, so a per-second RASTER line
    // exposes the per-frame conversion cost — the suspected VideoCore
    // IV bottleneck. Reset each SAMPLE.
    qint64 rasterConvertUsAccum = 0;
    qint64 rasterConvertCount = 0;

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
    qint64 framesForwarded = 0;
    qint64 framesRendered = 0;
    // Source frame rate as fps×1000. On the overlay path it's latched from
    // a GStreamer pad probe (streaming thread) and read by sampleStats
    // (GUI thread), so the handoff is atomic — a plain double would be a
    // data race / UB. Access via containerFps() / setContainerFps().
    QAtomicInteger<int> containerFpsMilli { 0 };
    qreal containerFps() const
    {
        return containerFpsMilli.loadRelaxed() / 1000.0;
    }
    void setContainerFps(qreal fps)
    {
        containerFpsMilli.storeRelaxed(qRound(fps * 1000.0));
    }

    // Intermediate sink between QMediaPlayer and the QML
    // VideoOutput's sink — the pacing gate's tap point (see
    // onVideoFrameDelivered). Owned by this widget.
    QVideoSink* pacingSink = nullptr;
    // True when the scene graph has rendered since the last frame
    // was forwarded — i.e. the VideoOutput is ready for new damage.
    // Starts true so the first frame always shows. All touch points
    // (onSceneRendered, onVideoFrameDelivered) run on the GUI thread:
    // QQuickWidget renders via QQuickRenderControl on the GUI thread
    // and the queued videoFrameChanged delivery lands there too, so
    // plain members are race-free.
    bool sceneReadyForFrame = true;
    // Single-slot mailbox: the newest frame that arrived while the
    // scene was still rendering. Forwarded (and cleared) from
    // onSceneRendered() so renders chain back-to-back at capacity
    // instead of idling until the next sink delivery.
    QVideoFrame pendingFrame;

    // Cap on /data/.anthias/playback-stats.log size. 8 MB ≈ a full
    // 24 h burn-in's worth of SAMPLE lines at 1 Hz; past that we
    // truncate on the next viewer start so a long-running device
    // doesn't fill its 15 GB SD card with stats. The log is
    // best-effort instrumentation, not durable history.
    static constexpr qint64 kMaxStatsLogBytes = 8 * 1024 * 1024;
};
