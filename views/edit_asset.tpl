% try:
	% start_info = asset_info["start_date"]
	% start_date = start_info.split(' @ ')[0]
	% start_time = start_info.split(' @ ')[1]
	% start_hour = start_time.split(':')[0]
	% start_minute = start_time.split(':')[1]
% except:
	% start_date = ""
	% start_time = ""
	% start_info = ""
	% start_hour = ""
	% start_minute = ""
% end

% try:
	% end_info = asset_info["end_date"]
	% end_date = end_info.split(' @ ')[0]
	% end_time = end_info.split(' @ ')[1]
	% end_hour = end_time.split(':')[0]
	% end_minute = end_time.split(':')[1]
% except:
	% end_date = ""
	% end_time = ""
	% end_info = ""
	% end_hour = ""
	% end_minute = ""
% end

<head>
	<title>Screenly Edit Asset</title>
	<link type="text/css" href="/static/css/style.css" rel="Stylesheet" />
	<link type="text/css" href="/static/css/ui-lightness/jquery-ui-1.8.23.custom.css" rel="Stylesheet" />
	<script type="text/javascript" src="/static/js/jquery-1.8.0.min.js"></script>
	<script type="text/javascript" src="/static/js/jquery-ui-1.8.23.custom.min.js"></script>
	<script type="text/javascript" src="/static/js/jquery-ui-timepicker-addon.js"></script>
	<script>
		$(function() {
			$( "#start" ).datetimepicker({
				separator: ' @ ',
				hour: {{start_hour}},
				minute: {{start_minute}},
				dateFormat: 'yy-mm-dd',
				firstDay: 1,
			});

			$( "#end" ).datetimepicker({
				separator: ' @ ',
				hour: {{end_hour}},
				minute: {{end_minute}},
				dateFormat: 'yy-mm-dd',
				firstDay: 1,
			});
		});
	</script>
</head>
<body>
	<div class="main">
		<h1>Screenly :: Edit Asset</h1>
		<p>
			<strong>Free Space</strong> (on "/"): {{free_space}}
			<br />
		</p>
		<fieldset class="main">
			<form action="/update_asset" name="asset" method="post">
				<p>
					<input type="hidden" id="asset_id" name="asset_id" value="{{asset_info["asset_id"]}}" />
				</p>
				<p>
					<strong><label for="name">Name: </label></strong>
					<input type="text" id="name" name="name" value="{{asset_info["name"]}}"/>
				</p>
				<p>
					<strong><label for="uri">URI: </label></strong>
					<input type="text" id="uri" name="uri" value="{{asset_info["uri"]}}"/>
				</p>
				<p>
					<strong><label for="duration">Duration: </label></strong>
					<input type="text" id="duration" name="duration" value="{{asset_info["duration"]}}"/>
				</p>
				<p>
					<strong><label for="mimetype">Resource type: </label></strong>
					<select id="mimetype" name="mimetype">
						<option value="{{asset_info["mimetype"]}}">{{asset_info["mimetype"]}}</option>
						<option value="image">Image</option>
						<option value="video">Video</option>
						<option value="web">Website</option>
					</select>
				</p>
				<p>
					<strong><label for="start">Start:</label></strong>
					<input id="start" type="textbox" name="start" value="{{start_info}}"/>
				</p>
				<p>
					<strong><label for="end">End:</label></strong>
					<input id="end" type="textbox" name="end" value="{{end_info}}"/>
				</p>
				<p>
					<strong><label for="is_cached">Cache Asset Locally?: </label></strong>
					<input type="checkbox" id="is_cached" name="is_cached" {{asset_info["is_cached"]}}/>
				</p>
				<p>
					<div class="aligncenter">
						<input type="submit" value="Submit" />
					</div>
				</p>
			</form>
		</fieldset>
		<a href="/delete_asset/{{asset_info["asset_id"]}}">Delete asset</a>
	</div>
	<div class="footer">
		<a href="/">Back</a>
	</div>
</body>
