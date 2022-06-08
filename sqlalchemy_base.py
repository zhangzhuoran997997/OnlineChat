from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import sqlalchemy_classes
import os

# 设置数据库
SQL_URL = 'sqlite:///foo.sqlite3'
# all class base
Base = declarative_base()
# init the connection with database
engine = create_engine(SQL_URL)
# 创建DBSession类型
Session = sessionmaker(bind=engine)
