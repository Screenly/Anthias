import db
import queries
import datetime

FIELDS = ["day", "time"]

create_shutdown_table = 'CREATE TABLE shutdown(day integer, time timestamp)'

def mkdict(keys):
    """Returns a function that creates a dict from a database record."""
    return lambda row: dict([(keys[ki], v) for ki, v in enumerate(row)])
	
def create(conn, shutdown):
	with db.commit(conn) as c:
		c.execute(queries.create_shutdown(list(shutdown.keys())), list(shutdown.values()))
	return shutdown

def read(conn, keys=FIELDS):
	shutdown_times = []
	mk = mkdict(keys)
	with db.cursor(conn) as c:
		c.execute(queries.read_shutdown(keys))
		shutdown_times = [mk(shutdown) for shutdown in c.fetchall()]
	return shutdown_times

def delete(conn, day, time):
	with db.commit(conn) as c:
		c.execute(queries.remove_shutdown, [(day, time)])