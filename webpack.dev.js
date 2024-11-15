const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const path = require("path");

module.exports = [
    {
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
                },
            ]
        }
    },
    {
        devtool: "source-map",
        mode: "development",
        entry: {
            "anthias": "./static/sass/anthias.scss",
        },
        module: {
            rules: [
                {
                    test: /\.scss$/,
                    use: [
                        MiniCssExtractPlugin.loader,
                        "css-loader",
                        "sass-loader"
                    ]
                }
            ]
        },
        plugins: [
            new MiniCssExtractPlugin({
                filename: "[name].css"
            })
        ],
        output: {
            path: path.resolve(__dirname, "static/css"),
        }
    }
];
