# flake8: noqa

from __future__ import unicode_literals

comma = ','.join


def quest(values):
    return '=?,'.join(values) + '=?'


def quest_2(values, c):
    return ', '.join([('%s=CASE ' % x) + ("WHEN asset_id=? THEN ? " * c) + 'ELSE asset_id END' for x in values])


exists_table = "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"


def read_all(keys):
    return 'select ' + comma(keys) + ' from assets order by play_order'


def read(keys):
    return 'select ' + comma(keys) + ' from assets where asset_id=?'


def create(keys):
    return 'insert into assets (' + comma(keys) + ') values (' + comma(['?'] * len(keys)) + ')'


remove = 'delete from assets where asset_id=?'


def update(keys):
    return 'update assets set ' + quest(keys) + ' where asset_id=?'


def multiple_update(keys, count):
    return 'UPDATE assets SET ' + quest(keys) + ' WHERE asset_id IN (' + comma(['?'] * count) + ')'


def multiple_update_not_in(keys, count):
    return 'UPDATE assets SET ' + quest(keys) + ' WHERE asset_id NOT IN (' + comma(['?'] * count) + ')'


def multiple_update_with_case(keys, count):
    return 'UPDATE assets SET ' + quest_2(keys, count) + \
        ' WHERE asset_id IN (' + comma(['?'] * count) + ')'
