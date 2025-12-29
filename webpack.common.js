const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const path = require("path");
const webpack = require('webpack');
const TsconfigPathsPlugin = require('tsconfig-paths-webpack-plugin');

module.exports = {
  entry: {
    "anthias": "./static/src/index.tsx",
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
        test: /\.tsx?$/,
        use: 'ts-loader',
        exclude: [/node_modules/, /src\/test/],
      },
      {
        test: /\.(js|jsx|mjs)$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: [
              '@babel/preset-env',
              '@babel/preset-react',
            ]
          }
        }
      },
      {
        test: /\.scss$/,
        use: [
          MiniCssExtractPlugin.loader,
          "css-loader",
          {
            loader: "sass-loader",
            options: {
              sassOptions: {
                quietDeps: true,
                silenceDeprecations: ['import', 'global-builtin', 'color-functions', 'if-function']
              }
            }
          }
        ]
      }
    ]
  },
  resolve: {
    plugins: [new TsconfigPathsPlugin()],
    extensions: ['.js', '.jsx', '.ts', '.tsx']
  }
};
