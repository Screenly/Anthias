const path = require("path");

module.exports = {
    devtool: "source-map",
    mode: "development",
    entry: {
        "anthias": "./static/js/anthias.coffee",
        "settings": "./static/js/settings.coffee",
    },
    output: {
        path: path.resolve(__dirname, "static/js"),
        filename: "[name].js"
    },
    module: {
        rules: [
            {
                test: /\.coffee$/,
                use: ["coffee-loader"]
            }
        ]
    },
};
