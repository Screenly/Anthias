% from datetime import datetime
<head>
    <title>Screenly - View Playlist</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />	
</head>
<body>
    <div class="main">
        <h1>Screenly :: View Playlist</h1>
        <table class="center">
        <tr><th>Name</th><th>Start date</th><th>End date</th><th>Duration</th><th>URI</th><th>Edit</th></tr>
        % for asset in nodeplaylist:
            % # Only include assets with a start_date
            % if asset["start_date"]:
                
                    % if len(asset["uri"]) > 30:
                        % uri=asset["uri"][0:30] + "..."
                    % else:
                        % uri=asset["uri"]
                    % end
                    
                    % if asset["start_date"]: 
                        % start_date = asset["start_date"]
                    % else:
                        % start_date = ""
                    % end
                
                    % if asset["end_date"]:
                        % end_date = asset["end_date"]
                    % else:
                        % end_date = "None"
                    % end
                    
                    <tr><td>{{asset["name"]}}</td><td>{{start_date}}</td><td>{{end_date}}</td><td>{{asset['duration']}}</td><td><a href="{{asset['uri']}}">{{uri}}</a></td><td><a href="/edit_asset/{{asset['asset_id']}}">Edit</a></td></tr>
                    % end
            %end
        </table>
    </div>
    <div class="footer">
        <a href="/">Back</a>
    </div>
</body>
