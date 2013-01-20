# -*- coding: utf8 -*-


def black_page():
    filepath = "/tmp/screenly_html/black_page.html"
    html = "<html><head><style>body {background-color:#000000;}</style></head><!-- Just a black page --></html>"
    f = open(filepath, 'w')
    f.write(html)
    f.close()
    return filepath


def image_page(image, asset_id):
    full_filename = '/tmp/screenly_html/' + asset_id + '.html'
    html = "<html><head><style>body {background-image:url('%s'); background-repeat:no-repeat; background-position:center; background-color:#000000;}</style></head><!-- Just a black page --></html>" % image
    f = open(full_filename, 'w')
    f.write(html)
    f.close()
    return full_filename
