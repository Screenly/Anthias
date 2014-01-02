
comma = ','.join
quest = lambda l: '=?,'.join(l) + '=?'

exists_table = "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"

read_all = lambda keys: 'select ' + comma(keys) + ' from assets order by play_order'
read = lambda keys: 'select ' + comma(keys) + ' from assets where asset_id=?'
create = lambda keys: 'insert into assets (' + comma(keys) + ') values (' + comma(['?'] * len(keys)) + ')'
remove = 'delete from assets where asset_id=?'
update = lambda keys: 'update assets set ' + quest(keys) + ' where asset_id=?'

exists_table_schedule = "SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'"

read_schedule = lambda keys: 'select ' + comma(keys) + ' from schedules where asset_id=?'
create_schedule = lambda keys: 'insert into schedules (' + comma(keys) + ') values (' + comma(['?'] * len(keys)) + ')'
update_schedule = lambda keys: 'update schedules set ' + quest(keys) + ' where id=?'
remove_schedule = 'delete from schedules where id=?'