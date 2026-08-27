import sqlite3
import sys

sys.path.insert(0, r"F:\卓望\配链接项目\执行端代码\执行端打包文件夹")
from core import executor as ex

ex.init_db()
print(ex.DB_PATH)
conn = sqlite3.connect(ex.DB_PATH)
print(conn.execute("select name from sqlite_master where type='table' order by name").fetchall())
conn.close()
