import QtQuick
import QtMultimedia

// VideoView's scene: a single VideoOutput on a black backdrop.
// VideoOutput is the only public Qt 6 video item whose frames render
// through the RHI scene graph (YUV→RGB happens in a fragment shader
// at composite time) — no QVideoFrame::toImage() GPU→CPU readback,
// no raster blit. Issue #2967 measured the prior
// QGraphicsVideoItem-on-raster-viewport path at 8–12 presented fps
// on Pi 4/Pi 5 with a saturated GUI thread; this path keeps every
// frame on the GPU.
//
// ``orientation`` is driven from C++ (VideoView::applyRotation) for
// the legacy ``video-rotate`` option — a defensive no-op in
// production since every platform now rotates the whole screen at
// the compositor / QPA layer.
Rectangle {
    color: "black"

    VideoOutput {
        id: videoOutput
        objectName: "videoOutput"
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectFit
    }
}
