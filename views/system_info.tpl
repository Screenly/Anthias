<head>
    <title>System Info</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />      
</head>
<body>
    <div class="main">
        <h1>System Info</h1>
        <p>
            <strong>Load Average</strong>: {{loadavg}}<br />
            <strong>Free Space</strong> (on "/"): {{free_space}}<br />
            <strong>Uptime</strong>: {{uptime}}<br />
            <strong>Resolution</strong>: {{resolution}}<br />
        </p>
        <h2>Viewer Log</h2>
        <div class="left">
        <p>
            % for line in viewlog:
            <small>{{line}}</small><br />
            % end
        </p>
        </div>
    </div>
    <div class="footer">
    <a href="/">Back</a>
    </div>
</body>
