create_assets_table = """
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
nocache integer default 0)
"""

comma = ','.join
quest = lambda l: '=?,'.join(l)+'=?'

read_all = lambda keys:'select '+comma(keys)+' from assets order by name'
read = lambda keys:'select '+comma(keys)+' from assets where asset_id=?'
create = lambda keys:'insert into assets ('+comma(keys)+ ') values ('+comma(['?']*len(keys))+')'
remove = 'delete from assets where asset_id=?'
update = lambda keys:'update assets set '+quest(keys)+' where asset_id=?'
