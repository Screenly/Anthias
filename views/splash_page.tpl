<head>
    <title>Welcome to Screenly</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />	
</head>
<body>
    <div class ="main">
        <h1>Welcome to Screenly</h1>
	% if ip_lookup:
        <p>To manage the content on this screen,<br /> just point your browser to:</p>
        <p><a href="{{url}}">{{url}}</a>
	% else:
	<p>Unable to resolve management IP.</p>
	% end

    </div>
    <div class="footer">
    <small>Brought to you by WireLoad Inc.</small>
    </div>
</body>
