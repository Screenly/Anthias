unix:!android {
    isEmpty(target.path) {
        target.path = $$(PREFIX)$${TARGET}_bin
        export(target.path)
    }
    INSTALLS += target
}

export(INSTALLS)
