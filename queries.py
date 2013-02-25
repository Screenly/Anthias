exists_assets_table = """
SELECT name FROM sqlite_master WHERE type='table' AND name='assets'
"""
create_assets_table = """
CREATE TABLE assets (asset_id TEXT, name TEXT, uri TEXT, md5 TEXT, start_date TIMESTAMP, end_date TIMESTAMP, duration TEXT, mimetype TEXT, is_enabled INTEGER default 0, nocache INTEGER default 0)
"""

comma = lambda l: ','.join(l)
quest = lambda l: '=?,'.join(l)+'=?'

read_all = lambda keys:'SELECT '+comma(keys)+' FROM assets ORDER BY name'
read = lambda keys:'SELECT '+comma(keys)+' FROM assets WHERE asset_id=?'
create = lambda keys:'INSERT INTO assets ('+comma(keys)+ ') VALUES ('+comma(['?']*len(keys))+')'
remove = 'DELETE FROM assets WHERE asset_id=?'
update = lambda keys:'UPDATE assets SET '+quest(keys)+' WHERE asset_id=?'
