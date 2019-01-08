var webPage = require('webpage');
var page = webPage.create();
var url = 'http://localhost:8081/static/spec/runner.html';

page.onConsoleMessage = function(msg, lineNum, sourceId) {
  console.log(msg);
};

page.onInitialized = function() {
  page.evaluate(function() {
    window.inPhantom = true;
  });
};

page.open(url, function (status) {
    if(status !== 'success') {
        console.log('cannot load page ' + url);
        phantom.exit(1);
    }
});

page.onConfirm = function(status) {
    phantom.exit(status);
};
