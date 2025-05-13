from fastapi import FastAPI, Depends, WebSocket, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import uuid
import logging
import os
import json
import asyncio
from database import SessionLocal, init_db
from models import User, Message
from manager import manager
from schemas import UserCreate, Token
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Настройка путей
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Статические файлы
app.mount(
    "/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static"
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

# Глобальная очередь сообщений
message_queue = asyncio.Queue()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class MessageRequest(BaseModel):
    message: str
    sender: str


@app.get("/", response_class=HTMLResponse)
async def main_page(request: Request):
    return templates.TemplateResponse(
        "started_page.html",
        {"request": request},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        "login_page.html",
        {"request": request},
    )


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse(
        "chat_page.html",
        {"request": request},
    )


@app.post("/register")
async def register(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    hashed_password = pwd_context.hash(user.password)
    new_user = User(username=user.username, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    return {"access_token": f"token_{user.username}"}


@app.post("/login")
async def login(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if not db_user or not pwd_context.verify(user.password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect credentials")
    return {"access_token": f"token_{user.username}"}


@app.get("/anonymous")
async def generate_guest_id():
    return {"guest_id": f"guest_{uuid.uuid4().hex[:8]}"}


@app.get("/messages")
async def get_messages(db: Session = Depends(get_db)):
    messages = db.query(Message).order_by(Message.timestamp.desc()).limit(50).all()
    return [
        {
            "sender": msg.sender,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
        }
        for msg in reversed(messages)
    ]


@app.post("/send_message")
async def send_message(request: MessageRequest, db: Session = Depends(get_db)):
    new_message = Message(
        sender=request.sender, content=request.message, timestamp=datetime.now()
    )
    db.add(new_message)
    db.commit()

    await message_queue.put({"sender": request.sender, "content": request.message})
    return {"status": "ok"}


@app.get("/get_messages")
async def get_messages_stream(last_id: int = 0):
    async def message_generator():
        current_id = last_id
        while True:
            if not message_queue.empty():
                message = await message_queue.get()
                current_id += 1
                yield f"data: {json.dumps({'id': current_id, **message})}\n\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(
        message_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    await manager.connect(websocket)
    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
                sender = data.get("sender", "Аноним")
                message = data.get("message", "").strip()

                if not message:
                    continue

                # Сохранение в БД
                db_message = Message(content=f"{sender}: {message}", user_id=None)
                db.add(db_message)
                db.commit()

                # Отправка всем
                await manager.broadcast(f"{sender}: {message}")
            except json.JSONDecodeError:
                await manager.broadcast("Ошибка: Неверный формат сообщения")
    except Exception as e:
        manager.disconnect(websocket)
        logger.error(f"WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)
