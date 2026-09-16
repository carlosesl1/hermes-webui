"""Sidebar aggregates must use available covering indexes, not message payload pages."""
import sqlite3
import pytest
from api import agent_sessions, models


def make_db(path, index_sql='CREATE INDEX cover ON messages(session_id,timestamp)'):
    with sqlite3.connect(path) as c:
        c.executescript('''
          CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,title TEXT,started_at REAL,
              parent_session_id TEXT,ended_at REAL,end_reason TEXT,message_count INTEGER);
          CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);
          CREATE INDEX competing ON messages(session_id,id);
          INSERT INTO sessions VALUES('p','webui','Parent',1,NULL,2,'compression',1);
          INSERT INTO sessions VALUES('a','webui','A',2,'p',NULL,NULL,1);
          INSERT INTO sessions VALUES('b','webui','B',3,'p',NULL,NULL,1);
          INSERT INTO messages VALUES(1,'p','user','original',1);
          INSERT INTO messages VALUES(2,'a','assistant','answer',4);
          INSERT INTO messages VALUES(3,'b','assistant','newer',5);
        ''')
        if index_sql:
            c.execute(index_sql)
        c.execute('ANALYZE')
        # Reproduce a cost estimate preferring the non-covering index, as in a
        # large live DB. No production stats/index are changed by this fix.
        c.execute("UPDATE sqlite_stat1 SET stat='1000000 100000' WHERE idx!='competing'")
        c.execute("UPDATE sqlite_stat1 SET stat='3 1' WHERE idx='competing'")
        c.execute('ANALYZE sqlite_schema')


def track(monkeypatch, module):
    plans=[]
    class Cursor(sqlite3.Cursor):
        def execute(self, sql, params=()):
            if 'COUNT(*) AS actual_message_count' in sql:
                plan=self.connection.execute('EXPLAIN QUERY PLAN '+sql,params).fetchall()
                plans.extend(str(row[3]) for row in plan)
            return super().execute(sql,params)
    class Conn(sqlite3.Connection):
        def cursor(self):
            return super().cursor(factory=Cursor)
    monkeypatch.setattr(module,'open_state_db_readonly',lambda p:sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,factory=Conn))
    return plans


@pytest.mark.parametrize('reader,module',[(models._read_state_db_sidebar_overrides,models),(agent_sessions.read_session_lineage_metadata,agent_sessions)])
def test_both_sidebar_readers_cover_stats_without_payload_reads(tmp_path,monkeypatch,reader,module):
    p=tmp_path/'state.db';make_db(p)
    plans=track(monkeypatch,module)
    first=reader(p,{'a','b'})
    assert set(first)=={'a','b'}
    assert any('COVERING INDEX cover' in plan for plan in plans),plans
    # No cache or frozen statistics: next read must observe a new message/tip.
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO messages VALUES(4,'a','assistant','latest',6)")
    second=reader(p,{'a','b'})
    if module is models:
        assert first['a']['_state_db_message_count']==1
        assert second['a']['_state_db_message_count']==2
        assert second['a']['_state_db_last_message_at']==6
    else:
        assert first['a']['_lineage_tip_id']=='b'
        assert second['a']['_lineage_tip_id']=='a'


@pytest.mark.parametrize('index_sql',[
    '',
    'CREATE INDEX partial_cover ON messages(session_id,timestamp) WHERE role=\'user\'',
    'CREATE INDEX expression_cover ON messages(lower(session_id),timestamp)',
    'CREATE INDEX "quoted\"\"name" ON messages(session_id,timestamp)',
])
def test_old_or_partial_or_quoted_index_keeps_exact_metadata(tmp_path,monkeypatch,index_sql):
    p=tmp_path/'state.db';make_db(p,index_sql)
    values=models._read_state_db_sidebar_overrides(p,{'a','b','p'})
    assert values['a']['_state_db_message_count']==1
    assert values['b']['_state_db_last_message_at']==5
    result=agent_sessions.read_session_lineage_metadata(p,{'a','b'})
    assert result['a']['_lineage_tip_id']=='b'


def test_missing_timestamp_and_schema_change_are_not_cached(tmp_path):
    p=tmp_path/'old.db'
    with sqlite3.connect(p) as c:
        c.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,title TEXT,message_count INTEGER);CREATE TABLE messages(session_id TEXT);INSERT INTO sessions VALUES('a','webui','A',0);INSERT INTO messages VALUES('a');CREATE INDEX sid_cover ON messages(session_id)")
    assert models._read_state_db_sidebar_overrides(p,{'a'})['a']['_state_db_message_count']==1
    with sqlite3.connect(p) as c:
        c.execute('ALTER TABLE messages ADD COLUMN timestamp REAL')
        c.execute("UPDATE messages SET timestamp=7")
        c.execute('DROP INDEX sid_cover')
        c.execute('CREATE INDEX new_cover ON messages(session_id,timestamp)')
    assert models._read_state_db_sidebar_overrides(p,{'a'})['a']['_state_db_last_message_at']==7
