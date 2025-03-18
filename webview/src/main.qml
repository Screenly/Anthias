import QtQuick
import QtQuick.Window
import QtQuick.Controls
import Qt.labs.platform

// Handle Qt version-specific imports
import QtWebEngine

Window {
    id: root
    visible: true
    visibility: Window.FullScreen
    color: "black"

    // Pre-loader for images
    WebEngineView {
        id: preLoader
        visible: false
        onLoadingChanged: function(loadInfo) {
            // Qt 6 changed the loadingChanged signal parameter
            if ((loadInfo.status === WebEngineView.LoadSucceededStatus) ||  // Qt 6
                (loadInfo.status === WebEngineLoadingInfo.LoadSucceededStatus)) {  // Qt 5
                webView.loadHtml(preLoader.html)
            }
        }
    }

    WebEngineView {
        id: webView
        anchors.fill: parent

        // Common settings for both Qt 5 and Qt 6
        settings {
            javascriptEnabled: true
            fullScreenSupportEnabled: true
            screenCaptureEnabled: true
            playbackRequiresUserGesture: false
            showScrollBars: false
            localStorageEnabled: true
        }

        onLoadingChanged: function(loadInfo) {
            // Qt 6 changed the loadingChanged signal parameter
            if ((loadInfo.status === WebEngineView.LoadSucceededStatus) ||  // Qt 6
                (loadInfo.status === WebEngineLoadingInfo.LoadSucceededStatus)) {  // Qt 5
                webView.runJavaScript("document.documentElement.style.overflow = 'hidden';")
            }
        }

        // Authentication handling is the same in both versions
        onAuthenticationRequired: function(request) {
            request.cancel()
            loadHtml(screenlyInterface.getAccessDeniedPage())
        }

        Component.onCompleted: {
            url = "about:blank"
        }
    }

    Connections {
        target: screenlyInterface

        function onLoadPageRequested(url) {
            webView.stop()
            webView.url = url
            webView.clearFocus()
        }

        function onLoadImageRequested(imagePath) {
            webView.stop()

            let src = ""
            if (screenlyInterface.isLocalFile(imagePath)) {
                // Convert local file path to nginx URL as in the C++ version
                const fileName = imagePath.split('/').pop()
                src = "http://anthias-nginx/screenly_assets/" + fileName
            } else if (imagePath === "null") {
                // Handle black page case
                webView.loadHtml("<html><body style='background: black;'></body></html>")
                return
            } else {
                src = imagePath
            }

            // Use the same image loading script as the C++ version
            const script = "window.setimg=function(n){var o=new Image;o.onload=function()" +
                         "{document.body.style.backgroundSize=o.width>window.innerWidth||o.height>window.innerHeight?\"contain\":\"auto\",document.body.style.backgroundImage=\"url('\"+n+\"')\"},o.src=n};"
            const styles = "background: #000 center no-repeat"

            preLoader.loadHtml(
                "<html><head><script>" + script + "</script></head>" +
                "<body style='" + styles + "'>" +
                "<script>window.setimg(\"" + src + "\");</script></body></html>"
            )

            webView.clearFocus()
        }
    }
}
