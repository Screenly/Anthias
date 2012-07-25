import os

def black_page():
    filepath = "/tmp/screenly_html/black_page.html"
    html = "<html><head><style>body {background-color:#000000;}</style></head><!-- Just a black page --></html>"
    f = open(filepath, 'w')
    f.write(html)
    f.close()
    return filepath

def image_page(image, name):
    filepath = "/tmp/screenly_html/" + name.replace(' ', '') + ".html"
    html = "<html><head><style>body {background-image:url('%s'); background-repeat:no-repeat; background-position:center; background-color:#000000;}</style></head><!-- Just a black page --></html>" % image
    f = open(filepath, 'w')
    f.write(html)
    f.close()
    return filepath



            