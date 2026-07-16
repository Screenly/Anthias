#include "rotation.h"

#include <QByteArray>
#include <QList>

namespace rotation
{

int linuxfbRotationOverride()
{
    // eglfs / wayland rotate the whole compositor output, so the image is
    // already turned by the platform and must NOT be rotated again here.
    // Only linuxfb needs a manual rotation: its QPA plugin parses but
    // does not act on the ``rotation=N`` option, so the Python side bakes
    // the angle into QT_QPA_PLATFORM=linuxfb:...,rotation=N and we honour
    // it in paintEvent.
    const QByteArray qpa = qgetenv("QT_QPA_PLATFORM");
    if (!qpa.startsWith("linuxfb")) {
        return 0;
    }
    const int colon = qpa.indexOf(':');
    if (colon < 0) {
        return 0;
    }
    const QList<QByteArray> options = qpa.mid(colon + 1).split(',');
    for (const QByteArray& option : options) {
        if (!option.startsWith("rotation=")) {
            continue;
        }
        bool ok = false;
        const int raw = option.mid(9).toInt(&ok);
        if (!ok) {
            return 0;
        }
        const int normalised = ((raw % 360) + 360) % 360;
        if (normalised == 90 || normalised == 180 || normalised == 270) {
            return normalised;
        }
        return 0;
    }
    return 0;
}

QString webpageRotationScript(int angle)
{
    // 90/270 swap the viewport box so a landscape page fills the
    // portrait-turned panel (pillar-boxed, like the image/video paths);
    // 180 keeps the box. Applied as an !important stylesheet so it wins
    // over the page's own rules, and re-asserted from a subtree
    // MutationObserver so a page that removes/replaces the injected
    // <style> node anywhere in the tree (SPAs rewriting <head>) can't
    // drop the rotation. Injected in an isolated world (ApplicationWorld)
    // so it never collides with the page's own scripts, but still mutates
    // the shared DOM.
    const QString box =
        (angle % 180 != 0)
            ? QStringLiteral(
                  "position:fixed;top:calc((100vh - 100vw)/2);"
                  "left:calc((100vw - 100vh)/2);width:100vh;height:100vw;")
            : QStringLiteral("width:100vw;height:100vh;");
    return QStringLiteral(
               "(function(){"
               "var css='html{transform:rotate(%1deg) !important;"
               "transform-origin:50% 50% !important;"
               "overflow:hidden !important;%2}';"
               "function apply(){"
               "var s=document.getElementById('anthias-rotation');"
               "if(!s){s=document.createElement('style');"
               "s.id='anthias-rotation';"
               "(document.head||document.documentElement).appendChild(s);}"
               "if(s.textContent!==css){s.textContent=css;}}"
               "apply();"
               "try{new MutationObserver(apply).observe("
               "document.documentElement,{childList:true,subtree:true});}"
               "catch(e){}"
               "})();")
        .arg(angle)
        .arg(box);
}

}  // namespace rotation
