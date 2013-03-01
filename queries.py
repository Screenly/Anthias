
comma = ','.join
quest = lambda l: '=?,'.join(l) + '=?'

exists_table = "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"

read_all = lambda keys: 'select ' + comma(keys) + ' from assets order by name'
read = lambda keys: 'select ' + comma(keys) + ' from assets where asset_id=?'
create = lambda keys: 'insert into assets (' + comma(keys) + ') values (' + comma(['?'] * len(keys)) + ')'
remove = 'delete from assets where asset_id=?'
update = lambda keys: 'update assets set ' + quest(keys) + ' where asset_id=?'
