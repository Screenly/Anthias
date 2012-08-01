% if asset["start_date"]:
	% start_date = asset["start_date"].split("T")[0]
	% start_time = asset["start_date"].split("T")[1]
% else:
	% start_date = ""
	% start_time = ""
% end

% if asset["end_date"]:
	% end_date = asset["end_date"].split("T")[0]
	% end_time = asset["end_date"].split("T")[1]
% else:
	% end_date = ""
	% end_time = ""
% end	

<head>
    <title>Screenly Edit Asset</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />	
</head>
<body>
    <div class="main">
        <h1>Screenly :: Edit Asset</h1>
            <fieldset class="main">
        	<form action="/update_asset" name="asset" method="post">
    		    <input type="hidden" id="asset_id" name="asset_id" value="{{asset["asset_id"]}}" /></p>
        		<p><strong><label for="name">Name: </value></strong>
        		    <input type="text" id="name" name="name" value="{{asset["name"]}}"/></p>
        		<p><strong><label for="uri">URI: </value></strong>
        		    <input type="text" id="uri" name="uri" value="{{asset["uri"]}}"/></p>
        		<p><strong><label for="duration">Duration: </value></strong>
    		        <input type="text" id="duration" name="duration" value="{{asset["duration"]}}"/></p>
            	<p><strong><label for="mimetype">Resource type: </value></strong>
            	<select id="mimetype" name="mimetype">
                        <option value="{{asset["mimetype"]}}">{{asset["mimetype"]}}</name>
                        <option value=""></name>
                        <option value="image">Image</name>
                        <option value="video">Video</name>
                        <option value="web">Website</name>
                    </select></p>
        		<p><strong><label for="start_date">Start Date: </value></strong>
    		        <input type="text" id="start_date" name="start_date" value="{{start_date}}"/></p>
                <p><strong><label for="start_time">Start Time: </value></strong>
        		    <input type="text" id="start_time" name="start_time" value="{{start_time}}"/></p>
        		<p><strong><label for="end_date">End Date: </value></strong>
    		        <input type="text" id="end_date" name="end_date" value="{{end_date}}"/></p>
        		<p><strong><label for="end_time">End Time: </value></strong>
    		        <input type="text" id="end_time" name="end_time" value="{{end_time}}"/></p>
        		<p><div class="aligncenter"><input type="submit" value="Submit" /></div></p>
        	</form>
            </fieldset>
    
            <a href="/delete_asset/{{asset["asset_id"]}}">Delete asset</a>
        </div>
        <div class="footer">
            <a href="/">Back</a>
        </div>
</body>
