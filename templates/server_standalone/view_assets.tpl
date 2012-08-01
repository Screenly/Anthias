% from datetime import datetime
<head>
    <title>Screenly - View Assets</title>
    <link type="text/css" href="/static/css/style.css" rel="Stylesheet" />	
</head>
<body>
    <div class="main">
        <h1>Screenly :: View Assets</h1>
        <table class="center">
        <tr><th>Name</th><th>Start date</th><th>End date</th><th>Duration</th><th>URI</th><th>Edit</th></tr>
        % for asset in nodeplaylist:
        	% if len(asset["uri"]) > 30:
                        % uri=asset["uri"][0:30] + "..."
                % else:
                        % uri=asset["uri"]
                % end
                    
                % if asset["start_date"]: 
                	% input_start_date=asset["start_date"].split("T")
                        % start_date=input_start_date[0]
                        % start_time=input_start_date[1]
                % else:
                        % start_date = "None"
                        % start_time = ""
                % end
                
                % if asset["end_date"]:
                        % input_end_date=asset["end_date"].split("T")
                        % end_date=input_end_date[0]
                        % end_time=input_end_date[1]
                % else:
                        % end_date = "None"
                        % end_time = ""
                % end
                    
                <tr><td>{{asset["name"]}}</td><td>{{start_date}} {{start_time}}</td><td>{{end_date}} {{end_time}}</td><td>{{asset['duration']}}</td><td><a href="{{asset['uri']}}">{{uri}}</a></td><td><a href="/edit_asset/{{asset['asset_id']}}">Edit</a></td></tr>
            %end
        </table>
    </div>
    <div class="footer">
        <a href="/">Back</a>
    </div>
</body>
