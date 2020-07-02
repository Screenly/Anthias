import db
import queries
from datetime import datetime,time,timedelta

FIELDS = ["id", "day", "time"]

create_shutdown_table = 'CREATE TABLE shutdown(id integer primary key autoincrement, day integer, time time)'

def mkdict(keys):
    """Returns a function that creates a dict from a database record."""
    return lambda row: dict([(keys[ki], v) for ki, v in enumerate(row)])
	
def create(conn, shutdown):
	with db.commit(conn) as c:
		c.execute(queries.create_shutdown(list(shutdown.keys())), list(shutdown.values()))
	return shutdown

def read(conn, shutdown_id=None, keys=FIELDS):
	shutdown_times = []
	mk = mkdict(keys)
	with db.cursor(conn) as c:
		if shutdown_id:
			c.execute(queries.read_shutdown_single(keys), [shutdown_id])
		else:
			c.execute(queries.read_shutdown(keys))
		shutdown_times = [mk(shutdown) for shutdown in c.fetchall()]
	return shutdown_times

def delete(conn, id):
	with db.commit(conn) as c:
		c.execute(queries.remove_shutdown, [(id)])

def determine_next(conn):
	shutdown_times = read(conn)
	shutdown_times.sort(key=lambda x: (x['day'],x['time']))
	if not shutdown_times:
		return None
	current_day = datetime.now().weekday()

	def convert_to_date(shut):
		today = datetime.now().today()
		ahead = shut['day'] - today.weekday()
		if ahead < 0 or (ahead == 0 and shut['time'] < datetime.now().time()):
			ahead += 7
		future_datetime = datetime.now() + timedelta(days=ahead)
		return future_datetime.replace(hour=shut['time'].hour,minute=shut['time'].minute,second=0,microsecond=0)

	shuts_today = list(filter(lambda x: x['day'] == current_day and x['time'] > datetime.now().time(), shutdown_times))
	if shuts_today:
		return convert_to_date(shuts_today[0])

	future = list(filter(lambda x: x['day'] >= current_day, shutdown_times))
	if future:
		return convert_to_date(future[0])

	past = list(filter(lambda x: x['day'] <= current_day, shutdown_times))
	if past:
		return convert_to_date(past[0])

	return None