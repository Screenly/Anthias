% from datetime import date, datetime
<head>
	<title>Screenly Submit Asset</title>
	<link type="text/css" href="/static/css/style.css" rel="Stylesheet" />
</head>
<body>
	<div class="main">
		<h1>Screenly :: Submit Asset</h1>
		<p>
			<strong>Free Space</strong> (on "/"): {{free_space}}
			<br />
		</p>
		<fieldset class="main">
			<form action="/process_asset" name="asset" method="post">
				<p>
					<strong><label for="name">Name: </label></strong>
					<input type="text" id="name" name="name" />
				</p>
				<p>
					<strong><label for="uri">URL: </label></strong>
					<input type="text" id="uri" name="uri" />
				</p>
				<p>
					<strong><label for="mimetype">Resource type: </label></strong>
					<select id="mimetype" name="mimetype">
						<option value="image">Image</option>
						<option value="video">Video</option>
						<option value="web">Website</option>
					</select>
				</p>
				<p>
					<strong><label for="is_cached">Cache Asset Locally?: </label></strong>
					<input type="checkbox" id="is_cached" name="is_cached" />
				</p>
				<p>
					<div class="aligncenter">
						<input type="submit" value="Submit" />
					</div>
				</p>
			</form>
		</fieldset>
	</div>
	<div class="footer">
		<a href="/">Back</a>
	</div>
</body>
