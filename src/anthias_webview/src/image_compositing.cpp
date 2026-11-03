#include "image_compositing.h"

#include <QRegion>

namespace image_compositing
{

QRect scaledImageRect(
    const QSize &imageSize, const QSize &box, bool originAtCentre)
{
    QSize scaledSize = imageSize;
    scaledSize.scale(box, Qt::KeepAspectRatio);
    if (originAtCentre) {
        return QRect(
            -scaledSize.width() / 2, -scaledSize.height() / 2,
            scaledSize.width(), scaledSize.height());
    }
    return QRect(
        (box.width() - scaledSize.width()) / 2,
        (box.height() - scaledSize.height()) / 2,
        scaledSize.width(), scaledSize.height());
}

void drawScaledImage(
    QPainter &painter, const QImage &image, const QSize &box,
    bool originAtCentre, qreal opacity)
{
    if (image.isNull() || opacity <= 0.0) {
        return;
    }
    if (opacity < 1.0) {
        painter.setOpacity(opacity);
    }
    painter.drawImage(
        scaledImageRect(image.size(), box, originAtCentre), image);
    if (opacity < 1.0) {
        painter.setOpacity(1.0);
    }
}

namespace
{
// See paintCrossfade()'s step 2 in image_compositing.h for the
// reasoning. Kept file-local: nothing outside paintCrossfade() has a
// reason to paint a gap on its own.
void fillLetterboxGaps(
    QPainter &painter, const QRect &incomingRect, const QSize &box,
    bool originAtCentre, qreal opacity)
{
    if (opacity <= 0.0) {
        return;
    }
    const QRect boxRect = originAtCentre
        ? QRect(
              -box.width() / 2, -box.height() / 2, box.width(),
              box.height())
        : QRect(0, 0, box.width(), box.height());
    const QRegion gap = QRegion(boxRect) - QRegion(incomingRect);
    if (gap.isEmpty()) {
        return;
    }
    painter.setOpacity(opacity);
    for (const QRect &r : gap) {
        painter.fillRect(r, Qt::black);
    }
    painter.setOpacity(1.0);
}
}  // namespace

void paintCrossfade(
    QPainter &painter, const QImage &outgoing, const QImage &incoming,
    const QSize &box, bool originAtCentre, qreal progress)
{
    drawScaledImage(painter, outgoing, box, originAtCentre, 1.0);
    fillLetterboxGaps(
        painter, scaledImageRect(incoming.size(), box, originAtCentre),
        box, originAtCentre, progress);
    drawScaledImage(painter, incoming, box, originAtCentre, progress);
}

}  // namespace image_compositing
