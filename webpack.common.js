const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const path = require("path");

module.exports = {
    entry: {
        "anthias": "./static/js/anthias.js",
        "settings": "./static/js/settings.js",
    },
    output: {
        path: path.resolve(__dirname, "static/dist"),
        filename: "js/[name].js",
        clean: true,
    },
    plugins: [
        new MiniCssExtractPlugin({
            filename: "css/anthias.css"
        })
    ],
    module: {
        rules: [
            {
                test: /\.js$/,
                exclude: /node_modules/,
                use: [
                    {
                        'loader': 'babel-loader',
                        'options': {
                            'presets': ['@babel/preset-env']
                        }
                    }
                ]
            },
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
};

