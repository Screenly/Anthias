% from datetime import date, datetime
<head>
    <title>Screenly Submit Asset</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />
</head>
<body>
    <div class="main">
        <h1>Screenly :: Submit Asset</h1>
            <fieldset class="main">
        	<form action="/process_asset" name="asset" method="post" enctype="multipart/form-data">
        		<p><strong><label for="name">Name: </label></strong>
        		    <input type="text" id="name" name="name" /></p>
        	<h2>Use public asset</h2>
		<p><strong><label for="uri">URL: </label></strong>
        		    <input type="text" id="uri" name="uri" /></p>
		<h2>Upload asset</h2>
		<p><input type="file" name="file_upload" /></p>
        		<p><strong><label for="mimetype">Resource type: </label></strong>
            	<select id="mimetype" name="mimetype">
                        <option value=""></option>
                        <option value="image">Image</option>
                        <option value="video">Video</option>
                        <option value="web">Website</option>
                    </select></p>
        		<p><div class="aligncenter"><input type="submit" value="Submit" /></div></p>
        	</form>
            </fieldset>
    </div>
    <div class="footer">
        <a href="/">Back</a>
    </div>
</body>
