# -*- coding: utf8 -*-


def black_page():
    filepath = "/tmp/screenly_html/black_page.html"
    html = "<html><head><style>body {background-color:#000000;}</style></head><!-- Just a black page --></html>"
    f = open(filepath, 'w')
    f.write(html)
    f.close()
    return filepath
