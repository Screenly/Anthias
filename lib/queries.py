
comma = ','.join
quest = lambda l: '=?,'.join(l) + '=?'
quest_2 = lambda l, c: ', '.join([('%s=CASE ' % x) + ("WHEN asset_id=? THEN ? " * c) + 'ELSE asset_id END' for x in l])

exists_table = "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"

read_all = lambda keys: 'select ' + comma(keys) + ' from assets order by play_order'
read = lambda keys: 'select ' + comma(keys) + ' from assets where asset_id=?'
create = lambda keys: 'insert into assets (' + comma(keys) + ') values (' + comma(['?'] * len(keys)) + ')'
remove = 'delete from assets where asset_id=?'
update = lambda keys: 'update assets set ' + quest(keys) + ' where asset_id=?'

multiple_update = lambda keys, count: \
    'UPDATE assets SET ' + quest(keys) + ' WHERE asset_id IN (' + comma(['?'] * count) + ')'
multiple_update_not_in = lambda keys, count: \
    'UPDATE assets SET ' + quest(keys) + ' WHERE asset_id NOT IN (' + comma(['?'] * count) + ')'

multiple_update_with_case = lambda keys, count: 'UPDATE assets SET ' + quest_2(keys, count) + \
                                                ' WHERE asset_id IN (' + comma(['?'] * count) + ')'
