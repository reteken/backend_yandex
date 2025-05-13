from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    password: str


class ChatCreate(BaseModel):
    name: str


class MessageCreate(BaseModel):
    content: str
    chat_id: int = 1
    is_anonymous: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
