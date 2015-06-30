# -*- coding: utf8 -*-

def black_page(filepath):
    html = """<html>
  <head>
    <script>
      window.setimg = function (uri) {
        var i = new Image();
        i.onload = function() {
          document.body.style.background = '#000000 url(' + uri + ') no-repeat center center fixed';
          document.body.style.backgroundSize = 'contain'
        }
        i.src = uri;
      }
    </script>
  </head>
  <body style="background: #000 center no-repeat"></body>
</html>"""

    with open(filepath, 'w') as f:
        f.write(html)
    return filepath


def image_page(uri, asset_id):
    full_filename = '/tmp/screenly_html/' + asset_id + '.html'
    html = """<html>
  <head>
    <script>
      scale = function () {
      var i = new Image(); i.src = '%s';
      document.body.style.backgroundSize = i.width > window.innerWidth || i.height > window.innerHeight ? 'contain' : 'auto';
      }
    </script>
  </head>
  <body style="background: #000 url(%s) center no-repeat" onload="scale()"></body>
</html>""" % (uri, uri)
    f = open(full_filename, 'w')
    f.write(html)
    f.close()
    return full_filename
