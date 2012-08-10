import sqlite3, os

# Define settings
configdir = os.path.join(os.getenv('HOME'), '.screenly/')
database = os.path.join(configdir, 'screenly.db')

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

    migration= """
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

else:
    pass

print "Migration done."
