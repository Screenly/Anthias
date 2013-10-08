# -*- coding: utf8 -*-


def black_page():
    filepath = "/tmp/screenly_html/black_page.html"
    html = "<html><head><style>body {background-color:#000000;}</style></head><!-- Just a black page --></html>"
    f = open(filepath, 'w')
    f.write(html)
    f.close()
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
