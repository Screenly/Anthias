#!/usr/bin/env python
# -*- coding: utf8 -*-

import sqlite3
import os
import shutil
import subprocess
from contextlib import contextmanager
import datetime

configdir = os.path.join(os.getenv('HOME'), '.screenly/')
database = os.path.join(configdir, 'screenly.db')

comma = ','.join
quest = lambda l: '=?,'.join(l) + '=?'
query_read_all = lambda keys: 'SELECT ' + comma(keys) + ' FROM assets ORDER BY name'
query_update = lambda keys: 'UPDATE assets SET ' + quest(keys) + ' WHERE asset_id=?'
mkdict = lambda keys: (lambda row: dict([(keys[ki], v) for ki, v in enumerate(row)]))


def is_active(asset):
    if asset['start_date'] and asset['end_date']:
        at = datetime.datetime.utcnow()
        return asset['start_date'] < at and asset['end_date'] > at
    return False


def read(c):
    keys = 'asset_id start_date end_date is_enabled'.split(' ')
    c.execute(query_read_all(keys))
    mk = mkdict(keys)
    assets = [mk(asset) for asset in c.fetchall()]
    return assets


def update(c, asset_id, asset):
    del asset['asset_id']
    c.execute(query_update(asset.keys()), asset.values() + [asset_id])


def test_column(col, cursor):
    """Test if a column is in the db"""
    try:
        cursor.execute('SELECT ' + col + ' FROM assets')
    except sqlite3.OperationalError:
        return False
    else:
        return True


@contextmanager
def open_db_get_cursor():
    with sqlite3.connect(database, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
        cursor = conn.cursor()
        yield (cursor, conn)
        cursor.close()

# ✂--------
query_add_ordinal = """
begin transaction;
alter table assets add ordinal integer default 0;
commit;
"""


def migrate_add_ordinal():
    with open_db_get_cursor() as (cursor, conn):
        col = 'ordinal'
        if test_column(col, cursor):
            print 'Column (' + col + ') already present'
        else:
            print 'Adding new column (' + col + ')'
            cursor.executescript(query_add_ordinal)
            assets = read(cursor)
            for asset in assets:
                asset.update({'ordinal': 0})
                update(cursor, asset['asset_id'], asset)
                conn.commit()
# ✂--------
query_create_assets_table = """
create table assets(
asset_id text primary key,
name text,
uri text,
md5 text,
start_date timestamp,
end_date timestamp,
duration text,
mimetype text,
is_enabled integer default 0,
nocache integer default 0)"""
query_make_asset_id_primary_key = """
begin transaction;
create table temp as select asset_id,name,uri,md5,start_date,end_date,duration,mimetype,is_enabled,nocache from assets;
drop table assets;
""" + query_create_assets_table + """;
insert or ignore into assets select * from temp;
drop table temp;
commit;"""


def migrate_make_asset_id_primary_key():
    has_primary_key = False
    with open_db_get_cursor() as (cursor, _):
        table_info = cursor.execute('pragma table_info(assets)')
        has_primary_key = table_info.fetchone()[-1] == 1
    if has_primary_key:
        print 'already has primary key'
    else:
        with open_db_get_cursor() as (cursor, _):
            cursor.executescript(query_make_asset_id_primary_key)
            print 'asset_id is primary key'
# ✂--------
query_add_is_enabled_and_nocache = """
begin transaction;
alter table assets add is_enabled integer default 0;
alter table assets add nocache integer default 0;
commit;
"""


def migrate_add_is_enabled_and_nocache():
    with open_db_get_cursor() as (cursor, conn):
        col = 'is_enabled,nocache'
        if test_column(col, cursor):
            print 'Columns (' + col + ') already present'
        else:
            cursor.executescript(query_add_is_enabled_and_nocache)
            assets = read(cursor)
            for asset in assets:
                asset.update({'is_enabled': is_active(asset)})
                update(cursor, asset['asset_id'], asset)
                conn.commit()
            print 'Added new columns (' + col + ')'
# ✂--------
query_drop_filename = """BEGIN TRANSACTION;
CREATE TEMPORARY TABLE assets_backup(asset_id, name, uri, md5, start_date, end_date, duration, mimetype);
INSERT INTO assets_backup SELECT asset_id, name, uri, md5, start_date, end_date, duration, mimetype FROM assets;
DROP TABLE assets;
CREATE TABLE assets(asset_id TEXT, name TEXT, uri TEXT, md5 TEXT, start_date TIMESTAMP, end_date TIMESTAMP, duration TEXT, mimetype TEXT);
INSERT INTO assets SELECT asset_id, name, uri, md5, start_date, end_date, duration, mimetype FROM assets_backup;
DROP TABLE assets_backup;
COMMIT;
"""


def migrate_drop_filename():
    with open_db_get_cursor() as (cursor, _):
        col = 'filename'
        if test_column(col, cursor):
            cursor.executescript(query_drop_filename)
            print 'Dropped obsolete column (' + col + ')'
        else:
            print 'Obsolete column (' + col + ') is not present'
# ✂--------


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
    except (OSError, IOError) as _:
        pass

    if not os.path.isfile(supervisor_symlink):
        try:
            subprocess.call(['/usr/bin/sudo', 'ln', '-s', new_target, supervisor_symlink])
        except (OSError, IOError) as _:
            print 'Failed to create symlink'

if __name__ == '__main__':
    migrate_drop_filename()
    migrate_add_is_enabled_and_nocache()
    migrate_make_asset_id_primary_key()
    migrate_add_ordinal()
    ensure_conf()
    fix_supervisor()
    print "Migration done."
