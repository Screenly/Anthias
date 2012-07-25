% from datetime import timedelta, datetime, time
<head>
    <title>Screenly Schedule Asset</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />	
</head>
<body>
    <div class="main">
        <h1>Screenly :: Schedule Asset</h1>
            <fieldset class="main">
        	<form action="/process_schedule" name="asset" method="post">
    
        		<p><strong><label for="asset">Asset: </value></strong>
                <select id="asset" name="asset">
                    <option value=""></name>
                    % for asset in assetlist:
                        % asset_id = asset[0]  
                        % name = asset[1]
                    <option value="{{asset_id}}">{{name}}</name>
                    % end
                </select></p>
                <p><strong><label for="start_date">Start date:</value></strong>
        		    <input id="start_date" type="textbox" name="start_date" value="{{datetime.now().strftime('%Y-%m-%d')}}" /></p>
                <p><strong><label for="start_time">Start time:</value></strong>
        		    <input id="start_time" type="textbox" name="start_time" value="{{datetime.now().strftime('%H:%M:%S')}}" /></p>
                <p><strong><label for="end_date">End date:</value></strong>
        		    <input id="end_date" type="textbox" name="end_date" value="{{(datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')}}" /></p>
                <p><strong><label for="start_time">End time:</value></strong>
            	    <input id="end_time" type="textbox" name="end_time" value="00:00:00" /></p>
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
