#pragma once

#include <QMainWindow>
#include <QWebEngineView>

#include "view.h"

class MainWindow : public QMainWindow
{
    Q_OBJECT

    public:
        explicit MainWindow();

    public slots:
        void loadPage(const QString &uri);
        void loadImage(const QString &uri);

    private:
        View *view;
};
