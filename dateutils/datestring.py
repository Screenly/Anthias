from datetime import date, datetime, timedelta
import pytz

def date_to_string(string):
    date = string.strftime('%Y-%m-%dT%H:%M:%S')
    return date

def string_to_date(string):
    date = datetime.strptime(string, '%Y-%m-%dT%H:%M:%S') 
    return date
    
def string_to_utc(input_date, input_time, input_timezone):
    timezone = pytz.timezone(input_timezone)
    timestamp = datetime.strptime(input_date+"T"+input_time, '%Y-%m-%dT%H:%M:%S')
    utc = pytz.utc
    localized_timestamp = timezone.localize(timestamp)
    utc_timestamp = localized_timestamp.astimezone(utc)
    
    return utc_timestamp