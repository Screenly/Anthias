% from datetime import timedelta, datetime, time
<head>
	<title>Screenly Schedule Asset</title>
	<link type="text/css" href="/static/css/style.css" rel="Stylesheet" />
	<link type="text/css" href="/static/css/ui-lightness/jquery-ui-1.8.23.custom.css" rel="Stylesheet" />	
	<script type="text/javascript" src="/static/js/jquery-1.8.0.min.js"></script>
	<script type="text/javascript" src="/static/js/jquery-ui-1.8.23.custom.min.js"></script>
	<script type="text/javascript" src="/static/js/jquery-ui-timepicker-addon.js"></script>
	<script>
	$(function() {
		$( "#start" ).datetimepicker({
		separator: ' @ ',
		hour: {{datetime.now().strftime('%H')}},
		minute: {{datetime.now().strftime('%M')}},
		dateFormat: 'yy-mm-dd',
		minDate: {{datetime.now().strftime('%Y-%m-%d')}},
		});

		$( "#end" ).datetimepicker({
		separator: ' @ ',
		hour: 23,
		minute: 59,
		dateFormat: 'yy-mm-dd',
		minDate: {{(datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')}}
		});
	});
	</script>
</head>
<body>
    <div class="main">
        <h1>Screenly :: Schedule Asset</h1>
            <fieldset class="main">
		<form action="/process_schedule" name="asset" method="post">
    
		<p><strong><label for="asset">Asset: </value></strong>
                <select id="asset" name="asset">
		<option value=""></option>
		% for asset in assets:
                    <option value="{{asset['asset_id']}}">{{asset['name']}}</option>
                % end
                </select></p>
                <p><strong><label for="start">Start:</value></strong>
			    <input id="start" type="textbox" name="start" value="{{datetime.now().strftime('%Y-%m-%d @ %H:%M')}}" /></p>
                <p><strong><label for="end">End:</value></strong>
			    <input id="end" type="textbox" name="end" value="{{(datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d') + " @ 23:59"}}" /></p>
                <p><strong><label for="duration">Duration:</value></strong>
                    <input id="duration" type="textbox" name="duration" value="5" /> In seconds. Only for images and web-pages.</p>
			<p><div class="aligncenter"><input type="submit" value="Submit" /></div></p>
		</form>
            </fieldset>
        </div>
        <div class="footer">
            <a href="/">Back</a>
        </div>
</body>
