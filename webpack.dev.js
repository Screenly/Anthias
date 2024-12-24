const { merge } = require("webpack-merge");
const common = require("./webpack.react.js");

module.exports = merge(common, {
  devtool: "source-map",
  mode: "development",
  devServer: {
    contentBase: "./static/dist",
    hot: true,
  },
});
