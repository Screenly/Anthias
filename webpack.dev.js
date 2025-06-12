const { merge } = require("webpack-merge");
const common = require("./webpack.common.js");
const webpack = require('webpack');

module.exports = merge(common, {
  devtool: "source-map",
  mode: "development",
  devServer: {
    contentBase: "./static/dist",
    hot: true,
  },
  plugins: [
    new webpack.DefinePlugin({
      'process.env.ENVIRONMENT': JSON.stringify('development')
    })
  ]
});
