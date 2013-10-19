# -*- coding: utf8 -*-

def black_page(filepath):
    html = """<html>
  <head>
    <script>
      window.setimg = function (uri) {
        var i = new Image();
        i.onload = function() {
          document.body.style.backgroundSize = i.width > window.innerWidth || i.height > window.innerHeight ? 'contain' : 'auto';
          document.body.style.backgroundImage = 'url(' + uri + ')';
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

