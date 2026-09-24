import pytest

from app.shared.sql.parser import SqlMigrationParser
from app.shared.sql.preflight_extractor import MigrationPreflightExtractor


def extract(sql):
    return MigrationPreflightExtractor().extract(SqlMigrationParser().parse(sql))


@pytest.mark.parametrize('action', ['CASCADE', 'RESTRICT', 'SET NULL', 'NO ACTION'])
def test_multiline_backticks_actions_and_composite(action):
    sql = f'''aLtEr TABLE `test`.`child`
    ADD CONSTRAINT `fk``name` FOREIGN KEY (`one`, `two`)
    REFERENCES `test`.`parent` (`id`, `second`)
    ON DELETE {action} ON UPDATE {action};'''
    op, = extract(sql)
    assert not op.error
    assert op.statement_sequence == 1
    assert op.foreign_key.child_columns == ('one', 'two')
    assert op.foreign_key.parent_columns == ('id', 'second')
    assert op.foreign_key.constraint_name == 'fk`name'
    assert op.schema == 'test'
    assert op.original_sql == sql.rstrip(';')


def test_multiple_adds_single_and_unnamed_fk():
    ops = extract('''ALTER TABLE c ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES p (id),
                     ADD FOREIGN KEY (b) REFERENCES p (id);
                     ALTER TABLE d ADD CONSTRAINT other FOREIGN KEY (x) REFERENCES p (id);''')
    assert [o.kind for o in ops] == ['ADD_FK'] * 3
    assert [o.statement_sequence for o in ops] == [1, 1, 2]
    assert ops[0].foreign_key.child_columns == ('a',)
    assert ops[1].constraint_name == ''


@pytest.mark.parametrize('sql', [
    'ALTER TABLE c ADD CONSTRAINT f FOREIGN KEY (id) REFERENCES',
    'ALTER TABLE c ADD CONSTRAINT f FOREIGN KEY (id) REFERENCES p (a,b)',
    'ALTER TABLE c ADD CONSTRAINT f FOREIGN KEY (id(2)) REFERENCES p (id)',
    'ALTER TABLE c ADD CONSTRAINT f FOREIGN KEY (id) REFERENCES p (id) MATCH FULL',
    'ALTER TABLE c ADD CONSTRAINT f FOREIGN KEY (id) REFERENCES p (id) ON DELETE SET DEFAULT',
    'ALTER TABLE c ADD CONSTRAINT f FOREIGN KEY (id REFERENCES p (id)',
    'CREATE TABLE c (id int, FOREIGN KEY (id) REFERENCES p(id))',
    'ALTER TABLE c MODIFY COLUMN x',
    'ALTER TABLE c DROP FOREIGN KEY',
    'ALTER TABLE c /*!80000 ADD FOREIGN KEY (id) REFERENCES p(id) */',
])
def test_malformed_or_unsupported_is_visible(sql):
    ops = extract(sql)
    assert ops and all(o.error for o in ops)


def test_drop_modify_change_sequence_and_literals():
    ops = extract('''SELECT 'MODIFY FOREIGN KEY';
    ALTER TABLE `c` DROP FOREIGN KEY `fk`;
    ALTER TABLE `c`
       MODIFY COLUMN `x` varchar(30) NOT NULL DEFAULT 'a,b',
       MODIFY `y` decimal(8,2);
    ALTER TABLE c CHANGE COLUMN old_name new_name int;
    ''')
    assert [(o.kind, o.statement_sequence) for o in ops] == [
        ('DROP_FK', 2), ('MODIFY', 3), ('MODIFY', 3), ('CHANGE', 4)]
    assert (ops[0].table, ops[0].constraint_name) == ('c', 'fk')
    assert [o.column for o in ops[1:]] == ['x', 'y', 'old_name']
    assert all(not o.error for o in ops)


def test_comments_do_not_create_candidates():
    ops = extract('''-- ALTER TABLE c MODIFY x int;
      SELECT 'FOREIGN KEY';
      ALTER /* ordinary comment */ TABLE c MODIFY x varchar(20) DEFAULT 'DROP FOREIGN KEY';''')
    assert len(ops) == 1 and ops[0].column == 'x'
