<head>
    <title>Welcome to Screenly</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />	
</head>
% mgmt_url = "http://" + my_ip + ":8080"
<body>
    <div class ="main">
        <h1>Welcome to Screenly</h1>
        <p>To manage the content on this screen,<br /> just point your browser to:</p>
        <p><a href="{{mgmt_url}}">{{mgmt_url}}</a>
    </div>
    <div class="footer">
    <small>Brought to you by WireLoad Inc.</small>
    </div>
</body>