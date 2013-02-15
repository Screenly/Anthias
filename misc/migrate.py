import sqlite3
import os
import shutil
import subprocess

# Define settings
configdir = os.path.join(os.getenv('HOME'), '.screenly/')
database = os.path.join(configdir, 'screenly.db')


### Migration for table 'filename'
try:
    conn = sqlite3.connect(database, detect_types=sqlite3.PARSE_DECLTYPES)
    c = conn.cursor()
    c.execute("SELECT filename FROM assets")
    filename_exist = c.fetchone()
    c.close()
except:
    filename_exist = False

# if the column 'filename' exist, drop it
if filename_exist:
    conn = sqlite3.connect(database, detect_types=sqlite3.PARSE_DECLTYPES)
    c = conn.cursor()

    migration = """
        BEGIN TRANSACTION;
        CREATE TEMPORARY TABLE assets_backup(asset_id, name, uri, md5, start_date, end_date, duration, mimetype);
        INSERT INTO assets_backup SELECT asset_id, name, uri, md5, start_date, end_date, duration, mimetype FROM assets;
        DROP TABLE assets;
        CREATE TABLE assets(asset_id TEXT, name TEXT, uri TEXT, md5 TEXT, start_date TIMESTAMP, end_date TIMESTAMP, duration TEXT, mimetype TEXT);
        INSERT INTO assets SELECT asset_id, name, uri, md5, start_date, end_date, duration, mimetype FROM assets_backup;
        DROP TABLE assets_backup;
        COMMIT;
        """

    c.executescript(migration)
    c.close()
    print "Dropped obsolete column (filename)"

# Ensure config file is in place
conf_file = os.path.join(os.getenv('HOME'), '.screenly', 'screenly.conf')
if not os.path.isfile(conf_file):
    print "Copying in config file..."
    example_conf = os.path.join(os.getenv('HOME'), 'screenly', 'misc', 'screenly.conf')
    shutil.copy(example_conf, conf_file)

incorrect_supervisor_symlink = '/etc/supervisor/conf.d/supervisor_screenly.conf'
if os.path.isfile(incorrect_supervisor_symlink):
    subprocess.call(['/usr/bin/sudo', 'rm', incorrect_supervisor_symlink])

# Updating symlink for supervisor
supervisor_symlink = '/etc/supervisor/conf.d/screenly.conf'
old_target = '/home/pi/screenly/misc/screenly.conf'
new_target = '/home/pi/screenly/misc/supervisor_screenly.conf'

try:
    supervisor_target = os.readlink(supervisor_symlink)
    if supervisor_target == old_target:
        subprocess.call(['/usr/bin/sudo', 'rm', supervisor_symlink])
except:
    pass

if not os.path.isfile(supervisor_symlink):
    try:
        subprocess.call(['/usr/bin/sudo', 'ln', '-s', new_target, supervisor_symlink])
    except:
        print 'Failed to create symlink'

print "Migration done."
