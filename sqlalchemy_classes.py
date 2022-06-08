from sqlalchemy import (
    Integer,
    Column,
    String,
    TIMESTAMP,
    ForeignKey,
    Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, synonym
from sqlalchemy_base import Base, Session, engine
from datetime import datetime
from uuid import uuid4
from server_helpers import hash_password

# 中间表 —— 实现user和chat的多对多（一个用户可以有多个对话框，一个对话框可以包含多个用户）
class UsersChats(Base):
    __tablename__ = "users_chats"
    user_id = Column(String(150), ForeignKey("users.id"), primary_key=True)
    chat_id = Column(String(150), ForeignKey("chats.id"), primary_key=True)
# 中间表 —— 实现user和circle的多对多（一个用户可以有多条朋友圈，一条朋友圈可以包含多个可见用户）
class UsersCircles(Base):
    __tablename__ = "users_circles"
    user_id = Column(String(150), ForeignKey("users.id"), primary_key=True)
    circle_id = Column(String(150), ForeignKey("circles.id"), primary_key=True)
# 中间表 —— 实现user和user之间的多对多（一个用户可以有多个朋友）
class Friendships(Base):
    __tablename__ = "friendships"
    user_a_id = Column(
        "user_a_id", String(100), ForeignKey("users.id"), index=True, primary_key=True
    )
    user_b_id = Column(
        "user_b_id", String(100), ForeignKey("users.id"), primary_key=True
    )
    created_at = Column("created_at", TIMESTAMP(), default=datetime.utcnow())
    UniqueConstraint("user_a_id", "user_b_id", name="unique_friendships")

# 单条信息
class Message(Base):
    __tablename__ = "messages"
    # 存储id，主键
    id = Column(String, primary_key=True, autoincrement=False)
    # 信息具体内容，长度不超过500
    message = Column(String(500), default="")
    # 信息创建时间
    created_at = Column("created_at", TIMESTAMP(), nullable=False)
    # 信息被读取时间
    read_at = Column("read_at", TIMESTAMP())
    # 属于哪个chat
    chat_id = Column(String, ForeignKey("chats.id"))
    # 由谁发送
    username = Column("username", String(50), nullable=False)
    # 发送者昵称
    userNickname = Column("userNickname", String(50), nullable=False)
    # 是否包含图片
    image = Column("image", String(150), nullable=True)

    def __init__(self, username, userNickname):
        self.created_at = datetime.utcnow()
        self.id = str(uuid4())
        self.username = username
        self.userNickname = userNickname


# 用户
class User(Base):
    __tablename__ = "users"

    # 存储id
    id = Column(String, primary_key=True, autoincrement=False)
    # 唯一标识用于登录的账号 （类似于微信号）不可修改
    username = Column("username", String(50), nullable=False)
    # 用户的昵称，可以修改
    userNickname = Column("userNickname", String(50), nullable=False)
    # 用户登陆的密码
    password = Column("password", String(500), nullable=False)
    # 用户实名认证
    firstname = Column("firstname", String(50), nullable=False)
    lastname = Column("lastname", String(50), nullable=False)
    # 用户邮件
    email = Column("email", String(100), nullable=False)
    # 账号创建时间
    created_at = Column("created_at", TIMESTAMP(), nullable=False)
    # 账号信息更新时间
    updated_at = Column("updated_on", TIMESTAMP(), nullable=False)
    # 上一次登录
    last_login = Column("last_login", TIMESTAMP(), default=None)
    # 搜索栏是否可见
    visible_in_searches = Column("visible_in_searches", Boolean, default=True)
    # 用户头像
    avatar = Column("avatar", String(500), nullable=True)
    # 用户拥有的chat
    chats = relationship("Chat", secondary="users_chats", back_populates="users", lazy="dynamic")
    # 用户拥有的好友
    friends = relationship(
        "User",
        secondary="friendships",
        primaryjoin=id == Friendships.user_a_id,
        secondaryjoin=id == Friendships.user_b_id,
        lazy="dynamic",
    )
    # 用户拥有的朋友圈
    circles = relationship("Circle", secondary="users_circles", back_populates="visiable", lazy="dynamic")
    # 用户拥有的提示信息
    Notices = relationship("Notice", lazy="dynamic")

    def __init__(self,userNickname, username, password, firstname, lastname, email):
        self.id = str(uuid4())
        self.username = username
        self.password = hash_password(password)
        self.userNickname = userNickname
        self.firstname = firstname
        self.lastname = lastname
        self.email = email
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def add_friend(self, friend):
        if friend not in self.friends:
            self.friends.append(friend)
            friend.friends.append(self)

    def remove_friend(self, friend):
        if friend in self.friends:
            self.friends.remove(friend)
            friend.friends.remove(self)

# 聊天框
class Chat(Base):
    __tablename__ = "chats"
    # 存储id
    id = Column(String(150), primary_key=True, autoincrement=False)
    # 聊天框群组的名字
    name = Column("chatname", String(50), nullable=False)
    # 聊天群创始人即群主的id
    createrID = Column(String(150),nullable=False)
    # 创建时间
    created_at = Column("created_at", TIMESTAMP(), nullable=False)
    # 包含的用户
    users = relationship(User, secondary="users_chats", back_populates="chats", lazy="dynamic")
    # 该聊天框中的所有信息
    messages = relationship(Message, lazy="dynamic")
    # 出现的最后一条信息
    last_message = Column("last_message", String(1000), nullable=True)
    # 最后一条信息的时间
    last_message_timestamp = Column(
        "last_message_timestamp", TIMESTAMP(), nullable=True
    )

    def __init__(self,name,createrID,id = str(uuid4())):
        self.name = name
        self.id = id
        self.createrID = createrID
        self.created_at = datetime.utcnow()

# 朋友圈
class Circle(Base):
    __tablename__ = "circles"
    # 存储id
    id = Column(String, primary_key=True, autoincrement=False)
    # 具体内容
    content = Column(String(500), default="")
    # 创建时间
    created_at = Column("created_at", TIMESTAMP(), nullable=False)
    # 用户昵称
    userNickname = Column(String, ForeignKey("users.userNickname"))
    # 用户id
    user_id = Column(String, ForeignKey("users.id"))
    # 可见该条朋友圈的人
    visiable = relationship(User, secondary="users_circles", back_populates="circles", lazy="dynamic")
    # 包含的图片
    image = Column("image", String(150), nullable=True)

    def __init__(self, userNickname, userid):
        self.created_at = datetime.utcnow()
        self.id = str(uuid4())
        self.userNickname = userNickname
        self.user_id = userid

# 提示
class Notice(Base):
    __tablename__ = "notices"
    # 存储id
    id = Column(String(150), primary_key=True, autoincrement=False)
    # 收到该notice的人的id
    recipient = Column(String, ForeignKey("users.id"))
    # 类型
    type = Column("type", String(50), nullable=False)
    # 发送notice的人
    sender = Column("sender", String(150), nullable=False)
    # 发送notice的人的昵称
    senderNickname = Column("senderNickname", String(150), nullable=False)
    # 头像
    avatar = Column("avatar", String, nullable=True)
    # 时间
    timestamp = Column("timestamp", TIMESTAMP(), nullable=False)
    # 已阅
    dismissed = Column("dismissed", Boolean, nullable=False)
    # 信息本身
    message = Column("message", String, nullable=False)

    def __init__(self, type, sender,senderNickname, message, avatar=None):
        self.id = str(uuid4())
        self.type = type
        self.sender = sender
        self.senderNickname = senderNickname
        self.message = message
        self.timestamp = datetime.utcnow()
        self.dismissed = False
        self.avatar = avatar

# Serialize all classes that inherit from Base into tables
Base.metadata.create_all(engine)
