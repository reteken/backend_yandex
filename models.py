from sqlalchemy import (
    Table,
    Column,
    Integer,
    String,
    ForeignKey,
    DateTime,
    Text,
    Boolean,
)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


chat_members = Table(
    "chat_members",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("chat_id", Integer, ForeignKey("chats.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True)
    password_hash = Column(String(200))
    messages = relationship("Message", back_populates="user")
    chats = relationship("Chat", secondary=chat_members, back_populates="members")


class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    members = relationship("User", secondary=chat_members, back_populates="chats")
    messages = relationship("Message", back_populates="chat")


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    content = Column(Text)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), default=1)
    is_anonymous = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="messages")
    chat = relationship("Chat", back_populates="messages")
