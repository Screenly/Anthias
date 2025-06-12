const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const path = require("path");
const webpack = require('webpack');

module.exports = {
  entry: {
    "anthias": "./static/src/index.js",
  },
  output: {
    path: path.resolve(__dirname, "static/dist"),
    filename: "js/[name].js",
    clean: true,
  },
  plugins: [
    new MiniCssExtractPlugin({
      filename: "css/anthias.css"
    }),
    new webpack.ProvidePlugin({
      React: 'react'
    }),
  ],
  module: {
    rules: [
      {
        test: /.(js|jsx|mjs)$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: [
              '@babel/preset-env',
              '@babel/preset-react'
            ]
          }
        }
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
  resolve: {
    alias: {
      '@/components': path.resolve(__dirname, 'static/src/components'),
      '@/constants': path.resolve(__dirname, 'static/src/constants.js'),
      '@/store': path.resolve(__dirname, 'static/src/store'),
      '@/sass': path.resolve(__dirname, 'static/sass'),
      '@/utils': path.resolve(__dirname, 'static/src/utils'),
    },
    extensions: ['.js', '.jsx']
  }
};
