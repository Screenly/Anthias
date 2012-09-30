% from datetime import date, datetime
<head>
    <title>Screenly Submit Asset</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />	
</head>
<body>
    <div class="main">
        <h1>Screenly :: Submit Asset</h1>
            <fieldset class="main">
        	<form action="/process_asset" name="asset" method="post">
        		<p><strong><label for="name">Name: </label></strong>
        		    <input type="text" id="name" name="name" /></p>
        		<p><strong><label for="amount">URL: </label></strong>
        		    <input type="text" id="value" name="uri" /></p>
        		<p><strong><label for="mimetype">Resource type: </label></strong>
            	<select id="mimetype" name="mimetype">
                        <option value=""></name>
                        <option value="image">Image</name>
                        <option value="video">Video</name>
                        <option value="web">Website</name>
                    </select></p>
        		<p><div class="aligncenter"><input type="submit" value="Submit" /></div></p>
        	</form>
            </fieldset>
    </div>
    <div class="footer">
        <a href="/">Back</a>
    </div>
</body>
