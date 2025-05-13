from fastapi import FastAPI, Depends, HTTPException, WebSocket, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from typing import Optional
import logging
import os
import uuid
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext

from database import SessionLocal, init_db
from models import User, Message, Chat, Base
from schemas import UserCreate, ChatCreate, MessageCreate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Настройки JWT
SECRET_KEY = "your-secret-key-123"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Настройки шаблонов
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount(
    "/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Инициализация БД
init_db()

# Создание общего чата
with SessionLocal() as db:
    if not db.query(Chat).filter(Chat.id == 1).first():
        general_chat = Chat(id=1, name="General Chat")
        db.add(general_chat)
        db.commit()

# Зависимости
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)
):
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        user = db.query(User).filter(User.username == username).first()
        return user
    except JWTError:
        return None


# Роуты
@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request):
    return templates.TemplateResponse("started_page.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login_page.html", {"request": request})


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("chat_page.html", {"request": request})


@app.get("/chat_list", response_class=HTMLResponse)
async def chat_list_page(request: Request):
    return templates.TemplateResponse("chat_list.html", {"request": request})


@app.post("/register")
async def register(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    hashed = pwd_context.hash(user.password)
    new_user = User(username=user.username, password_hash=hashed)
    db.add(new_user)
    db.commit()

    return {
        "access_token": create_access_token({"sub": new_user.username}),
        "token_type": "bearer",
        "username": new_user.username,
    }


@app.post("/login")
async def login(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if not db_user or not pwd_context.verify(user.password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    return {
        "access_token": create_access_token({"sub": db_user.username}),
        "token_type": "bearer",
        "username": db_user.username,
    }


@app.get("/anonymous")
async def generate_guest_id():
    return {"guest_id": f"guest_{uuid.uuid4().hex[:8]}"}


@app.post("/chats/")
async def create_chat(
    chat_data: ChatCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    new_chat = Chat(name=chat_data.name)
    new_chat.members.append(current_user)
    db.add(new_chat)
    db.commit()
    db.refresh(new_chat)
    return new_chat


@app.get("/chats/")
async def get_chats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return current_user.chats if current_user else []


@app.post("/send_message")
async def send_message(
    message: MessageCreate,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    if message.is_anonymous and not current_user:
        raise HTTPException(
            status_code=400, detail="Guests can't send non-anonymous messages"
        )

    new_message = Message(
        content=message.content,
        chat_id=message.chat_id,
        user_id=current_user.id if current_user else None,
        is_anonymous=message.is_anonymous,
    )
    db.add(new_message)
    db.commit()
    return {"status": "ok"}


@app.get("/messages")
async def get_messages(chat_id: int = 1, db: Session = Depends(get_db)):
    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.timestamp)
        .all()
    )
    return [
        {
            "sender": (
                "Anonymous"
                if msg.is_anonymous
                else (msg.user.username if msg.user else "Guest")
            ),
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
        }
        for msg in messages
    ]


@app.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket, chat_id: int = 1, db: Session = Depends(get_db)
):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            new_message = Message(content=data, chat_id=chat_id, is_anonymous=True)
            db.add(new_message)
            db.commit()
            await websocket.send_text(f"Anonymous: {data}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await websocket.close()
