import sqlite3
import os
import shutil
import subprocess

# Define settings
configdir = os.path.join(os.getenv('HOME'), '.screenly/')
database = os.path.join(configdir, 'screenly.db')
mkconn = lambda: sqlite3.connect(database, detect_types=sqlite3.PARSE_DECLTYPES)
def test_for_column(col,conn):
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT ' + col + ' FROM assets')
    except sqlite3.OperationalError as no_such:
        return False, cursor
    else:
        return True, cursor

def migrate_add_enabled_nocache():
    with mkconn() as conn:
        has_col, cursor = test_for_column('is_enabled,nocache',conn)
        if has_col:
            print "Columns is_enabled and nocache already present"
        else:
            migration = """
begin transaction;
alter table assets add is_enabled boolean default false;
alter table assets add nocache boolean default false;
commit;"""
            cursor.executescript(migration)
            print "Added columns is_enabled and nocache"
        cursor.close()

def migrate_drop_filename():
    """
    Migration for table 'filename'
    if the column 'filename' exist, drop it
    """
    with mkconn() as conn:
        has_col, cursor = test_for_column('filename',conn)
        if has_col:
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
            cursor.executescript(migration)
            print "Dropped obsolete column (filename)"
        cursor.close()
###


def ensure_conf():
    """Ensure config file is in place"""
    conf_file = os.path.join(os.getenv('HOME'), '.screenly', 'screenly.conf')
    if not os.path.isfile(conf_file):
        print "Copying in config file..."
        example_conf = os.path.join(os.getenv('HOME'), 'screenly', 'misc', 'screenly.conf')
        shutil.copy(example_conf, conf_file)

def fix_supervisor():
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
            pass#subprocess.call(['/usr/bin/sudo', 'ln', '-s', new_target, supervisor_symlink])
        except:
            print 'Failed to create symlink'

if __name__ == '__main__':
    migrate_drop_filename()
    migrate_add_enabled_nocache()
    ensure_conf()
    fix_supervisor()
    print "Migration done."
